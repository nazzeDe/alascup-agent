from __future__ import annotations

from src.tools._fingerprint import format_fingerprint


def search_experience(
    query: str,
    fingerprint: dict,
    store,
    embedder,
    top_k: int = 5,
) -> list[dict]:
    """Semantic search over the knowledge base.

    Embeds the query augmented with fingerprint key-value pairs so that
    entries with similar diagnostic fingerprints score higher.
    """
    text = query
    if fingerprint:
        text = f"{query} {format_fingerprint(fingerprint)}"
    embedding = embedder.embed(text)
    results = store.search(embedding, top_k=top_k)
    return [r for r in results if r.get("metadata", {}).get("valid", True)]
