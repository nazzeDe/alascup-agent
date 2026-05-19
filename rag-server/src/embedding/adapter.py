from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class APIEmbedder:
    """Call an OpenAI-compatible Embedding API (``POST /v1/embeddings``).

    No local model, no PyTorch — the embedding is computed server-side,
    making this adapter architecture-agnostic (x86_64 / loongarch64).
    """

    def __init__(self, api_base: str, model: str = "text-embedding-3-small"):
        self._api_base = api_base.rstrip("/")
        self._model = model

    def embed(self, text: str) -> list[float]:
        import json
        from urllib.error import URLError
        from urllib.request import Request, urlopen

        url = f"{self._api_base}/embeddings"
        body = json.dumps({"model": self._model, "input": text}).encode()
        req = Request(url, data=body, headers={
            "Content-Type": "application/json",
        })

        try:
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
        except URLError:
            logger.exception("Embedding API unreachable: %s", url)
            raise EmbeddingAPIError(f"Embedding API unreachable: {url}")
        except Exception:
            logger.exception("Embedding API call failed")
            raise EmbeddingAPIError("Embedding API call failed")

        try:
            return data["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError):
            raise EmbeddingAPIError(f"Unexpected embedding response: {data}")


class EmbeddingAPIError(RuntimeError):
    """Raised when the Embedding API returns an error or is unreachable."""
