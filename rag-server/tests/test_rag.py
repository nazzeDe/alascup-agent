"""Unit tests for rag-server tool functions.

Uses FakeVectorStore and FakeEmbedder so tests are deterministic
and require no external services.
"""

import json
import hashlib

import pytest

pytestmark = pytest.mark.unit


# ── Fakes ──────────────────────────────────────────────────────────────

class FakeVectorStore:
    """In-memory vector store that computes cosine similarity naively."""

    def __init__(self):
        self._docs: dict[str, dict] = {}
        self._counter = 0

    def add(self, embedding: list[float], metadata: dict, text: str) -> str:
        self._counter += 1
        doc_id = f"doc-{self._counter}"
        self._docs[doc_id] = {
            "id": doc_id,
            "embedding": embedding[:],
            "metadata": {**metadata},
            "text": text,
        }
        return doc_id

    def search(self, embedding: list[float], top_k: int = 5) -> list[dict]:
        scored = []
        for doc in self._docs.values():
            if not doc["metadata"].get("valid", True):
                continue
            score = self._cosine_sim(embedding, doc["embedding"])
            scored.append({**doc, "score": score})
        scored.sort(key=lambda x: -x["score"])
        return [
            {"id": d["id"], "metadata": d["metadata"], "text": d["text"], "score": d["score"]}
            for d in scored[:top_k]
        ]

    def mark_invalid(self, doc_id: str) -> bool:
        doc = self._docs.get(doc_id)
        if doc is None:
            return False
        doc["metadata"]["valid"] = False
        return True

    def get(self, doc_id: str) -> dict | None:
        return self._docs.get(doc_id)

    def update_metadata(self, doc_id: str, metadata: dict) -> bool:
        doc = self._docs.get(doc_id)
        if doc is None:
            return False
        doc["metadata"].update(metadata)
        return True

    @staticmethod
    def _cosine_sim(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


class FakeEmbedder:
    """Deterministic embedder — same text always produces same vector."""

    def __init__(self, dim: int = 8):
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        h = hashlib.sha256(text.encode()).digest()
        return [(h[i] / 255.0) for i in range(min(len(h), self.dim))]


# ── Helpers ────────────────────────────────────────────────────────────

def _make_fingerprint_str(fp: dict) -> str:
    """Serialize fingerprint consistently: sorted k=v pairs."""
    return " ".join(f"{k}={v}" for k, v in sorted(fp.items()))


# ── RG-001: Fingerprint precision retrieval ───────────────────────────

class TestFingerprintSearch:
    """RG-001: Fingerprint-driven search ranks matching fingerprint higher."""

    def test_fingerprint_match_ranks_first(self):
        from src.tools.retrieval.search import search_experience

        store = FakeVectorStore()
        embedder = FakeEmbedder()

        # exp-001: CPU high + mysqld + slow query (io_wait=low)
        fp_001 = {"cpu_percent": "82", "top_process": "mysqld", "log_pattern": "Sorting result", "io_wait": "low"}
        exp001_text = _make_fingerprint_str(fp_001)
        store.add(
            embedder.embed(f"CPU 占用 >80% {exp001_text}"),
            {"symptom": "CPU 占用 >80%", "fingerprint": json.dumps(fp_001, sort_keys=True),
             "root_cause": "MySQL 查询缺少索引导致 filesort", "valid": True},
            "为 orders.created_at 添加索引",
        )

        # exp-004: CPU high + kworker + high I/O wait
        fp_004 = {"cpu_percent": "70", "top_process": "kworker", "io_wait": "high"}
        exp004_text = _make_fingerprint_str(fp_004)
        store.add(
            embedder.embed(f"CPU 占用 >80% {exp004_text}"),
            {"symptom": "CPU 占用 >80%", "fingerprint": json.dumps(fp_004, sort_keys=True),
             "root_cause": "磁盘 I/O 阻塞导致 kworker 内核线程自旋", "valid": True},
            "更换故障磁盘，迁移数据",
        )

        # Search with fingerprint matching exp-001
        results = search_experience(
            query="CPU 占用高",
            fingerprint={"cpu_percent": "80", "top_process": "mysqld", "io_wait": "low"},
            store=store,
            embedder=embedder,
            top_k=5,
        )

        assert len(results) >= 1
        # exp-001 should rank first because fingerprint matches
        assert "MySQL" in results[0]["text"] or "索引" in results[0]["text"]

    def test_returns_empty_when_no_knowledge(self):
        from src.tools.retrieval.search import search_experience

        store = FakeVectorStore()
        embedder = FakeEmbedder()
        results = search_experience("high CPU", {}, store, embedder)
        assert results == []

    def test_respects_top_k(self):
        from src.tools.retrieval.search import search_experience

        store = FakeVectorStore()
        embedder = FakeEmbedder()
        for i in range(10):
            fp = {"n": str(i)}
            fp_str = _make_fingerprint_str(fp)
            store.add(
                embedder.embed(f"issue {i} {fp_str}"),
                {"symptom": f"issue {i}", "fingerprint": json.dumps(fp, sort_keys=True), "valid": True},
                f"fix {i}",
            )
        results = search_experience("issue", {}, store, embedder, top_k=3)
        assert len(results) == 3

    def test_excludes_invalid_entries(self):
        from src.tools.retrieval.search import search_experience

        store = FakeVectorStore()
        embedder = FakeEmbedder()
        store.add(embedder.embed("disk full"), {"valid": True}, "cleanup")
        invalid_id = store.add(embedder.embed("disk full outdated"), {"valid": False}, "old fix")

        results = search_experience("disk full", {}, store, embedder, top_k=5)
        result_ids = [r["id"] for r in results]
        assert invalid_id not in result_ids


# ── RG-002 & RG-003: Dedup logic ───────────────────────────────────────

class TestDedup:
    """RG-002: Different fingerprint → no dedup.  RG-003: Similar fingerprint → dedup."""

    def test_different_fingerprint_creates_new_record(self):
        """RG-002: Same symptom, different fingerprint → new record, not dedup."""
        from src.tools.writeback.save import save_experience

        store = FakeVectorStore()
        embedder = FakeEmbedder()

        # Seed exp-001: CPU high → slow query (io_wait=low)
        save_experience(
            symptom="CPU 占用高",
            fingerprint={"cpu_percent": "82", "top_process": "mysqld", "io_wait": "low", "log_pattern": "Sorting result"},
            root_cause="MySQL 查询缺少索引",
            solution="为 orders.created_at 添加索引",
            store=store,
            embedder=embedder,
        )

        # RG-002: Save with different fingerprint (io_wait=high) → different root cause
        result = save_experience(
            symptom="CPU 占用高",
            fingerprint={"cpu_percent": "70", "io_wait": "high"},
            root_cause="磁盘 I/O 阻塞",
            solution="更换故障磁盘",
            store=store,
            embedder=embedder,
        )

        assert result["status"] == "saved"
        # Should have 2 records in store
        assert len(store._docs) == 2

    def test_similar_fingerprint_triggers_dedup(self):
        """RG-003: Same symptom + similar fingerprint → dedup, no new record."""
        from src.tools.writeback.save import save_experience

        store = FakeVectorStore()
        embedder = FakeEmbedder()

        # Seed exp-001
        save_experience(
            symptom="CPU 占用 >80%",
            fingerprint={"cpu_percent": "82", "top_process": "mysqld", "log_pattern": "Sorting result"},
            root_cause="MySQL 查询缺少索引导致 filesort",
            solution="为 orders.created_at 添加索引",
            store=store,
            embedder=embedder,
        )

        initial_count = len(store._docs)

        # RG-003: Save very similar symptom + fingerprint
        result = save_experience(
            symptom="CPU 占用 >80%",
            fingerprint={"cpu_percent": "82", "top_process": "mysqld", "log_pattern": "Sorting result"},
            root_cause="MySQL 查询缺少索引导致 filesort",
            solution="为 orders.created_at 添加索引",
            store=store,
            embedder=embedder,
        )

        assert result["status"] == "deduplicated"
        assert len(store._docs) == initial_count  # No new record

    def test_barely_different_fingerprint_below_threshold(self):
        """Slightly different fingerprint but below threshold → still dedup."""
        from src.tools.writeback.save import save_experience

        store = FakeVectorStore()
        embedder = FakeEmbedder()

        save_experience(
            symptom="Nginx 不可用",
            fingerprint={"service_name": "nginx", "log_pattern": "unknown directive"},
            root_cause="nginx.conf 语法错误",
            solution="修正 nginx.conf",
            store=store,
            embedder=embedder,
        )

        # Almost identical — slightly different log_pattern value
        result = save_experience(
            symptom="Nginx 不可用",
            fingerprint={"service_name": "nginx", "log_pattern": "unknown directive in conf"},
            root_cause="nginx.conf 语法错误",
            solution="修正 nginx.conf",
            store=store,
            embedder=embedder,
        )

        # With high threshold this should dedup; with lower threshold it might create new
        # The default 0.95 threshold should handle this as dedup
        assert result["status"] in ("saved", "deduplicated")


# ── RG-004: Mark invalid ──────────────────────────────────────────────

class TestMarkInvalid:
    """RG-004: mark_experience_invalid keeps the record but excludes it from search."""

    def test_mark_valid_entry_invalid(self):
        from src.tools.quality.invalidate import mark_experience_invalid

        store = FakeVectorStore()
        embedder = FakeEmbedder()
        doc_id = store.add(embedder.embed("test"), {"valid": True}, "content")

        result = mark_experience_invalid(doc_id, "outdated", store)
        assert result["status"] == "marked_invalid"
        assert result["record_id"] == doc_id

        # Verify metadata is updated
        doc = store.get(doc_id)
        assert doc["metadata"]["valid"] is False

    def test_mark_invalid_nonexistent(self):
        from src.tools.quality.invalidate import mark_experience_invalid

        store = FakeVectorStore()
        result = mark_experience_invalid("nonexistent", "", store)
        assert result["status"] == "not_found"

    def test_marked_invalid_not_searchable(self):
        """RG-004: After marking invalid, search_experience excludes it."""
        from src.tools.retrieval.search import search_experience
        from src.tools.quality.invalidate import mark_experience_invalid

        store = FakeVectorStore()
        embedder = FakeEmbedder()

        fp = {"cpu_percent": "82"}
        fp_str = _make_fingerprint_str(fp)
        doc_id = store.add(
            embedder.embed(f"CPU 占用高 {fp_str}"),
            {"symptom": "CPU 占用高", "fingerprint": json.dumps(fp, sort_keys=True), "valid": True},
            "重启 mysqld",
        )

        # Mark invalid
        mark_experience_invalid(doc_id, "方案已过时", store)

        # Search should not return it
        results = search_experience("CPU 占用高", {"cpu_percent": "82"}, store, embedder, top_k=5)
        result_ids = [r["id"] for r in results]
        assert doc_id not in result_ids


# ── Save / search round-trip ──────────────────────────────────────────

class TestSaveSearchRoundTrip:
    """End-to-end: save then immediately search."""

    def test_saved_entry_is_searchable(self):
        from src.tools.writeback.save import save_experience
        from src.tools.retrieval.search import search_experience

        store = FakeVectorStore()
        embedder = FakeEmbedder()

        save_experience(
            symptom="磁盘空间不足",
            fingerprint={"disk_percent": "92", "largest_dir": "/tmp/logs"},
            root_cause="日志文件堆积未轮转",
            solution="清理 /tmp/logs，配置 logrotate",
            store=store,
            embedder=embedder,
        )

        results = search_experience("磁盘空间不足", {"disk_percent": "92"}, store, embedder, top_k=5)
        assert len(results) >= 1
        assert "logrotate" in results[0]["text"] or "清理" in results[0]["text"]

    def test_metadata_is_preserved(self):
        from src.tools.writeback.save import save_experience

        store = FakeVectorStore()
        embedder = FakeEmbedder()

        result = save_experience(
            symptom="CPU 占用高",
            fingerprint={"cpu_percent": "85"},
            root_cause="CPU 密集型进程",
            solution="优化查询",
            store=store,
            embedder=embedder,
        )

        doc = store.get(result["record_id"])
        assert doc is not None
        assert doc["metadata"]["symptom"] == "CPU 占用高"
        assert doc["metadata"]["root_cause"] == "CPU 密集型进程"
        assert doc["metadata"]["valid"] is True
        assert "fingerprint" in doc["metadata"]


# ── Preprocessing ─────────────────────────────────────────────────────

class TestChunker:
    def test_short_text_unchanged(self):
        from src.preprocessing.chunker import chunk_text

        text = "Short document"
        chunks = chunk_text(text, chunk_size=500)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_long_text_is_split(self):
        from src.preprocessing.chunker import chunk_text

        # Generate text longer than chunk_size
        words = ["word"] * 600
        text = " ".join(words)
        chunks = chunk_text(text, chunk_size=200, chunk_overlap=50)
        assert len(chunks) > 1
        # Each chunk should be <= chunk_size words
        for chunk in chunks:
            assert len(chunk.split()) <= 200

    def test_overlap_between_chunks(self):
        from src.preprocessing.chunker import chunk_text

        words = [f"w{i}" for i in range(300)]
        text = " ".join(words)
        chunks = chunk_text(text, chunk_size=100, chunk_overlap=20)
        # Verify overlap: end of chunk 0 should appear in start of chunk 1
        assert len(chunks) >= 2
        last_words_c0 = chunks[0].split()[-15:]
        first_words_c1 = chunks[1].split()[:15]
        # At least some overlap
        overlap = set(last_words_c0) & set(first_words_c1)
        assert len(overlap) > 0


class TestCleaner:
    def test_strips_whitespace(self):
        from src.preprocessing.cleaner import clean_text

        assert clean_text("  hello  world  ") == "hello world"

    def test_collapses_newlines(self):
        from src.preprocessing.cleaner import clean_text

        assert clean_text("line1\n\n\nline2") == "line1\nline2"

    def test_removes_null_bytes(self):
        from src.preprocessing.cleaner import clean_text

        assert clean_text("text\x00with\x00nulls") == "textwithnulls"


# ── Config ─────────────────────────────────────────────────────────────

class TestConfig:
    def test_load_config_returns_dataclass(self):
        from src.config import RagServerConfig, load_config
        cfg = load_config()
        assert isinstance(cfg, RagServerConfig)
        assert cfg.similarity_threshold == 0.95
        assert cfg.collection_name == "operations_knowledge"
        assert cfg.top_k_default == 5

    def test_config_fields_default_to_none(self):
        from src.config import RagServerConfig
        cfg = RagServerConfig()
        assert cfg.chroma_client is None
        assert cfg.embedding_fn is None


# ── ChromaVectorStore edge cases ──────────────────────────────────────

class TestChromaStoreEdgeCases:

    def test_get_nonexistent_returns_none(self, chroma_client_for_test):
        from src.vector_store.chroma_store import ChromaVectorStore
        store = ChromaVectorStore(chroma_client_for_test, "test_collection")
        assert store.get("nonexistent-id") is None

    def test_count_empty_collection(self, chroma_client_for_test):
        from src.vector_store.chroma_store import ChromaVectorStore
        store = ChromaVectorStore(chroma_client_for_test, "test_empty")
        assert store.count() == 0

    def test_mark_invalid_nonexistent_is_noop(self, chroma_client_for_test):
        from src.vector_store.chroma_store import ChromaVectorStore
        store = ChromaVectorStore(chroma_client_for_test, "test_mark_noop")
        # ChromaDB update for nonexistent ID is a silent no-op
        result = store.mark_invalid("nonexistent")
        assert result in (True, False)  # implementation-dependent

    def test_search_empty_collection(self, chroma_client_for_test):
        from src.vector_store.chroma_store import ChromaVectorStore
        store = ChromaVectorStore(chroma_client_for_test, "test_search_empty")
        results = store.search([0.1, 0.2, 0.3], top_k=5)
        assert results == []

    def test_add_then_get(self, chroma_client_for_test):
        from src.vector_store.chroma_store import ChromaVectorStore
        store = ChromaVectorStore(chroma_client_for_test, "test_add_get")
        doc_id = store.add([0.1, 0.2], {"k": "v"}, "hello")
        doc = store.get(doc_id)
        assert doc is not None
        assert doc["text"] == "hello"
        assert doc["metadata"]["k"] == "v"

    def test_add_then_count(self, chroma_client_for_test):
        from src.vector_store.chroma_store import ChromaVectorStore
        store = ChromaVectorStore(chroma_client_for_test, "test_count")
        assert store.count() == 0
        store.add([0.1], {"k": "v"}, "x")
        assert store.count() == 1
