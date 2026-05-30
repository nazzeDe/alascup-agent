from __future__ import annotations

import sys
from typing import Any

from fastmcp import FastMCP
from loguru import logger

from src.config import RagServerConfig, load_config
from src.embedding.adapter import APIEmbedder
from src.tools.quality.invalidate import mark_experience_invalid as _mark_invalid
from src.tools.retrieval.search import search_experience as _search
from src.tools.writeback.save import save_experience as _save
from src.vector_store.chroma_store import ChromaVectorStore


class _EmbedAdapter:
    """Wraps a bare embed function so tools can call ``embedder.embed(text)``."""
    __slots__ = ("embed",)

    def __init__(self, fn):
        self.embed = fn


TOOL_OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "search_experience": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "自然语言症状描述"},
            "fingerprint": {
                "type": "object",
                "description": "诊断指纹 (key:value 快照)，用于精准匹配；可选",
            },
            "top_k": {"type": "integer", "description": "返回结果数", "default": 5},
        },
    },
    "save_experience": {
        "type": "object",
        "properties": {
            "symptom": {"type": "string", "description": "观测到的症状"},
            "fingerprint": {"type": "object", "description": "诊断指纹 (key:value 快照)"},
            "root_cause": {"type": "string", "description": "根因"},
            "solution": {"type": "string", "description": "解决方案"},
        },
    },
    "mark_experience_invalid": {
        "type": "object",
        "properties": {
            "record_id": {"type": "string", "description": "要标记失效的经验 ID"},
            "reason": {"type": "string", "description": "失效原因（可选）"},
        },
    },
    "health": {
        "type": "object",
        "properties": {},
    },
}

# Module-level cache set by _probe_embedding_api.
_embedding_api_available = False


def create_server(config: RagServerConfig) -> FastMCP:
    store = ChromaVectorStore(
        client=config.chroma_client,
        collection_name=config.collection_name,
    )
    raw_fn = config.embedding_fn
    embedder = raw_fn if hasattr(raw_fn, "embed") else _EmbedAdapter(raw_fn)

    server = FastMCP(name="rag-server")

    @server.tool(
        name="search_experience",
        description="语义搜索历史运维经验。支持诊断指纹（key:value 快照）精准匹配。",
        output_schema=TOOL_OUTPUT_SCHEMAS["search_experience"],
    )
    def search_experience(
        query: str = "",
        fingerprint: dict | None = None,
        top_k: int = 5,
    ) -> dict:
        results = _search(
            query=query,
            fingerprint=fingerprint or {},
            store=store,
            embedder=embedder,
            top_k=top_k or config.top_k_default,
        )
        return {"results": results}

    @server.tool(
        name="save_experience",
        description="存储本次诊断路径与解决方案。指纹驱动去重——症状相同但指纹不同视为不同根因，创建新记录。",
        output_schema=TOOL_OUTPUT_SCHEMAS["save_experience"],
    )
    def save_experience(
        symptom: str = "",
        fingerprint: dict | None = None,
        root_cause: str = "",
        solution: str = "",
    ) -> dict:
        return _save(
            symptom=symptom,
            fingerprint=fingerprint or {},
            root_cause=root_cause,
            solution=solution,
            store=store,
            embedder=embedder,
            similarity_threshold=config.similarity_threshold,
        )

    @server.tool(
        name="mark_experience_invalid",
        description="标记失效经验（不物理删除，保留审计追溯）。失效经验不再被 search_experience 返回。",
        output_schema=TOOL_OUTPUT_SCHEMAS["mark_experience_invalid"],
    )
    def mark_experience_invalid(record_id: str = "", reason: str = "") -> dict:
        return _mark_invalid(record_id=record_id, reason=reason, store=store)

    @server.tool(
        name="health",
        description="Liveness 探针，返回服务状态和知识库规模",
        output_schema=TOOL_OUTPUT_SCHEMAS["health"],
    )
    def health() -> dict:
        return {
            "status": "healthy",
            "collection": config.collection_name,
            "entry_count": store.count(),
            "embedding_api_available": _embedding_api_available,
        }

    return server


def main() -> int:
    try:
        config = load_config()
        _init_chroma(config)
        _init_embedder(config)
        _probe_embedding_api(config)
        server = create_server(config)
        server.run(transport="streamable-http", host="0.0.0.0", port=11452)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        logger.exception("Failed to start rag-server")
        return 1


def _init_chroma(config: RagServerConfig) -> None:
    if config.chroma_client is not None:
        return
    import chromadb

    if config.chroma_mode == "memory":
        config.chroma_client = chromadb.Client()
    else:
        config.chroma_client = chromadb.PersistentClient(path=config.chroma_path)


def _init_embedder(config: RagServerConfig) -> None:
    if config.embedding_fn is not None:
        return
    emb = APIEmbedder(
        api_base=config.embedding_api_base,
        api_key=config.embedding_api_key,
    )
    config.embedding_fn = emb.embed


def _probe_embedding_api(config: RagServerConfig) -> None:
    """Ping the Embedding API once at startup to set ``embedding_api_available``."""
    global _embedding_api_available
    try:
        import httpx
        resp = httpx.get(
            config.embedding_api_base.rstrip("/"),
            headers={"Authorization": f"Bearer {config.embedding_api_key}"},
            timeout=5.0,
        )
        _embedding_api_available = resp.status_code < 500
    except Exception:
        _embedding_api_available = False


if __name__ == "__main__":
    raise SystemExit(main())
