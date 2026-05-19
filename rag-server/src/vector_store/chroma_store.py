from __future__ import annotations

from chromadb import Client as ChromaClient


class ChromaVectorStore:
    """ChromaDB-backed vector store for rag-server.

    Lazy-initialises the collection on first access so the client can be
    injected after construction (e.g. after fastmcp worker fork).
    """

    def __init__(self, client: ChromaClient, collection_name: str = "operations_knowledge"):
        self._client = client
        self._collection_name = collection_name
        self._collection: object | None = None

    def _get_collection(self):
        if self._collection is None:
            try:
                self._collection = self._client.get_collection(self._collection_name)
            except Exception:
                self._collection = self._client.create_collection(
                    self._collection_name,
                    metadata={"hnsw:space": "cosine"},
                )
        return self._collection

    def add(self, embedding: list[float], metadata: dict, text: str) -> str:
        import uuid

        doc_id = str(uuid.uuid4())
        col = self._get_collection()
        col.add(embeddings=[embedding], metadatas=[metadata], documents=[text], ids=[doc_id])
        return doc_id

    def search(self, embedding: list[float], top_k: int = 5) -> list[dict]:
        col = self._get_collection()
        try:
            results = col.query(query_embeddings=[embedding], n_results=top_k)
            if not results or not results.get("ids") or not results["ids"][0]:
                return []
            items: list[dict] = []
            for i, doc_id in enumerate(results["ids"][0]):
                meta = (results.get("metadatas") or [[{}]])[0][i] if results.get("metadatas") else {}
                text = (results.get("documents") or [[""]])[0][i] if results.get("documents") else ""
                distance = (results.get("distances") or [[1.0]])[0][i] if results.get("distances") else 0.0
                items.append({
                    "id": doc_id,
                    "metadata": meta,
                    "text": text,
                    "score": 1.0 - min(distance, 1.0),
                })
            return items
        except Exception:
            return []

    def mark_invalid(self, doc_id: str) -> bool:
        col = self._get_collection()
        try:
            col.update(ids=[doc_id], metadatas=[{"valid": False}])
            return True
        except Exception:
            return False

    def get(self, doc_id: str) -> dict | None:
        col = self._get_collection()
        try:
            result = col.get(ids=[doc_id])
            if result and result.get("ids"):
                i = 0
                return {
                    "id": result["ids"][i],
                    "metadata": (result.get("metadatas") or [{}])[i],
                    "text": (result.get("documents") or [""])[i],
                }
            return None
        except Exception:
            return None

    def count(self) -> int:
        col = self._get_collection()
        try:
            return col.count()
        except Exception:
            return 0
