from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable

from chromadb import Client as ChromaClient


@dataclass
class RagServerConfig:
    """RAG server configuration with dependency injection points.

    All external dependencies (ChromaDB client, embedding function) are
    injected so tests can swap in fakes without touching the filesystem.
    """

    chroma_client: ChromaClient | None = None
    embedding_fn: Callable[[str], list[float]] | None = None
    embedding_api_base: str = ""
    similarity_threshold: float = 0.95
    collection_name: str = "operations_knowledge"
    chroma_mode: str = "persistent"
    chroma_path: str = "./chroma_data"
    top_k_default: int = 5


def load_config() -> RagServerConfig:
    return RagServerConfig(
        embedding_api_base=os.getenv("EMBEDDING_API_BASE", "http://localhost:8080/v1"),
    )
