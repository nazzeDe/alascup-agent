from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Callable

from chromadb import Client as ChromaClient


@dataclass
class RagServerConfig:
    """RAG server configuration with dependency injection points.

    All external dependencies (ChromaDB client, embedding function) are
    injected so tests can swap in fakes without touching the filesystem.
    """

    # ── Hardcoded ──────────────────────────────────────────────────────
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    embedding_dimensions: int = 1024
    similarity_threshold: float = 0.90
    collection_name: str = "operations_knowledge"
    chroma_mode: str = "persistent"
    chroma_path: str = "./chroma_data"
    top_k_default: int = 5
    chunk_size: int = 8192
    chunk_overlap: int = 512
    batch_size: int = 64

    # ── Environment variable injection ─────────────────────────────────
    embedding_api_key: str = ""
    embedding_api_base: str = ""

    # ── Dependency injection (tests swap these) ────────────────────────
    chroma_client: ChromaClient | None = None
    embedding_fn: Callable[[str], list[float]] | None = None


def load_config() -> RagServerConfig:
    api_key = os.getenv("EMBEDDING_API_KEY", "")
    if not api_key:
        print("FATAL: EMBEDDING_API_KEY is required but not set", file=sys.stderr)
        sys.exit(1)

    return RagServerConfig(
        embedding_api_key=api_key,
        embedding_api_base=os.getenv("EMBEDDING_API_BASE", ""),
    )
