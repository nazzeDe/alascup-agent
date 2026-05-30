from __future__ import annotations

import tiktoken

_ENCODING_NAME = "o200k_base"


def _get_encoding():
    return tiktoken.get_encoding(_ENCODING_NAME)


def count_tokens(text: str) -> int:
    """Return token count using tiktoken o200k_base encoding."""
    enc = _get_encoding()
    return len(enc.encode(text))


def chunk_text(text: str, chunk_size: int = 8192, chunk_overlap: int = 512) -> list[str]:
    """Split text into overlapping token-based chunks.

    Uses tiktoken (o200k_base) to count tokens. Each chunk is at most
    ``chunk_size`` tokens, with ``chunk_overlap`` tokens shared between
    consecutive chunks.
    """
    if not text:
        return [text]

    enc = _get_encoding()
    tokens = enc.encode(text)
    if len(tokens) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(tokens):
        end = min(start + chunk_size, len(tokens))
        chunk_tokens = tokens[start:end]
        chunks.append(enc.decode(chunk_tokens))
        start += chunk_size - chunk_overlap if chunk_overlap < chunk_size else chunk_size
    return chunks
