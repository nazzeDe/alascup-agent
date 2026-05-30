from __future__ import annotations

import logging

import httpx
import tiktoken

logger = logging.getLogger(__name__)


class APIEmbedder:
    """Call SiliconFlow Embedding API (OpenAI-compatible ``POST /embeddings``).

    No local model, no PyTorch — the embedding is computed server-side,
    making this adapter architecture-agnostic (x86_64 / loongarch64).
    """

    MODEL = "Qwen/Qwen3-Embedding-0.6B"
    DIMENSIONS = 1024
    MAX_RETRIES = 3
    BATCH_SIZE = 64
    TIMEOUT = 10.0

    def __init__(self, api_base: str, api_key: str):
        self._client = httpx.Client(
            base_url=api_base.rstrip("/"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=self.TIMEOUT,
        )
        self._tokenizer = tiktoken.get_encoding("o200k_base")

    @property
    def model(self) -> str:
        return self.MODEL

    @property
    def dimensions(self) -> int:
        return self.DIMENSIONS

    def count_tokens(self, text: str) -> int:
        """Return token count using tiktoken o200k_base encoding."""
        return len(self._tokenizer.encode(text))

    def embed(self, text: str) -> list[float]:
        """Embed a single text, returning its vector."""
        return self._call_api(text)[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts, auto-splitting by ``BATCH_SIZE``."""
        if not texts:
            return []
        all_vectors: list[list[float]] = []
        for i in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[i : i + self.BATCH_SIZE]
            all_vectors.extend(self._call_api(batch))
        return all_vectors

    def _call_api(self, input_data: str | list[str]) -> list[list[float]]:
        """Send request with retry logic.

        Retries connection/timeout/5xx up to ``MAX_RETRIES`` times.
        Does not retry 401/403.
        """
        body = {
            "model": self.MODEL,
            "input": input_data,
            "dimensions": self.DIMENSIONS,
            "encoding_format": "float",
        }
        last_exc: Exception | None = None

        for attempt in range(1, self.MAX_RETRIES + 2):  # 1 initial + N retries
            try:
                resp = self._client.post("/embeddings", json=body)
                if resp.status_code in (401, 403):
                    logger.error("Embedding API auth failed: %s", resp.status_code)
                    raise EmbeddingAPIError(
                        f"Embedding API auth failed ({resp.status_code})"
                    )
                if resp.status_code == 429:
                    logger.error("Embedding API rate limited")
                    raise EmbeddingAPIError("Embedding API rate limited")
                if resp.status_code >= 500:
                    raise EmbeddingAPIError(
                        f"Embedding API server error ({resp.status_code})"
                    )
                resp.raise_for_status()
                data = resp.json()
                return [item["embedding"] for item in data["data"]]
            except EmbeddingAPIError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt <= self.MAX_RETRIES:
                    logger.warning(
                        "Embedding API retry %d/%d: %s",
                        attempt,
                        self.MAX_RETRIES,
                        exc,
                    )
                else:
                    logger.error(
                        "Embedding API unreachable after %d retries",
                        self.MAX_RETRIES,
                    )
            if attempt > self.MAX_RETRIES:
                raise EmbeddingAPIError(
                    f"Embedding API unreachable after {self.MAX_RETRIES} retries"
                ) from last_exc
        # Unreachable
        raise EmbeddingAPIError("Embedding API unreachable")


class EmbeddingAPIError(RuntimeError):
    """Raised when the Embedding API returns an error or is unreachable."""
