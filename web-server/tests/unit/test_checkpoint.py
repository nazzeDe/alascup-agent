import pickle
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_db():
    """Database with connected pool for checkpoint operations."""
    db = MagicMock()
    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock()
    mock_conn.fetchrow = AsyncMock()
    mock_conn.fetch = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    db.pool = mock_pool
    return db


def _config(thread_id="test-thread", checkpoint_ns="", checkpoint_id=None):
    c = {"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns}}
    if checkpoint_id:
        c["configurable"]["checkpoint_id"] = checkpoint_id
    return c


def _checkpoint_data(checkpoint_id=None):
    cid = checkpoint_id or str(uuid.uuid4())
    return {
        "id": cid,
        "v": 1,
        "ts": "2025-01-01T00:00:00Z",
        "channel_values": {},
        "channel_versions": {},
        "versions_seen": {},
    }


class TestPostgresCheckpointerConstruction:
    def test_pool_raises_when_not_connected(self):
        from src.persistence.checkpoint import PostgresCheckpointer

        db = MagicMock()
        db.pool = None
        cp = PostgresCheckpointer(db)
        with pytest.raises(RuntimeError, match="not connected"):
            _ = cp._pool


class TestAput:
    @pytest.mark.asyncio
    async def test_inserts_checkpoint(self, mock_db):
        from src.persistence.checkpoint import PostgresCheckpointer

        cp = PostgresCheckpointer(mock_db)
        config = _config(thread_id="t1", checkpoint_ns="ns1")
        ckpt = _checkpoint_data("ckpt-1")

        result = await cp.aput(config, ckpt, {"source": "test"}, {})

        conn = mock_db.pool.acquire.return_value.__aenter__.return_value
        conn.execute.assert_called_once()
        assert result["configurable"]["thread_id"] == "t1"
        assert result["configurable"]["checkpoint_id"] == "ckpt-1"

    @pytest.mark.asyncio
    async def test_uses_upsert_semantics(self, mock_db):
        from src.persistence.checkpoint import PostgresCheckpointer

        cp = PostgresCheckpointer(mock_db)
        config = _config(thread_id="t1")
        ckpt = _checkpoint_data("ckpt-1")

        await cp.aput(config, ckpt, {}, {})

        conn = mock_db.pool.acquire.return_value.__aenter__.return_value
        sql = conn.execute.call_args[0][0]
        assert "ON CONFLICT" in sql
        assert "DO UPDATE" in sql

    @pytest.mark.asyncio
    async def test_pickles_checkpoint_data(self, mock_db):
        from src.persistence.checkpoint import PostgresCheckpointer

        cp = PostgresCheckpointer(mock_db)
        ckpt = _checkpoint_data("ckpt-1")
        ckpt["channel_values"] = {"messages": ["msg1", "msg2"]}

        await cp.aput(_config(), ckpt, {}, {})

        conn = mock_db.pool.acquire.return_value.__aenter__.return_value
        args = conn.execute.call_args[0]
        # args: query, thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, pickled, meta_json
        pickled = args[5]
        restored = pickle.loads(pickled)
        assert restored["channel_values"] == {"messages": ["msg1", "msg2"]}


class TestAputWrites:
    @pytest.mark.asyncio
    async def test_deletes_old_writes_then_inserts_new(self, mock_db):
        from src.persistence.checkpoint import PostgresCheckpointer

        cp = PostgresCheckpointer(mock_db)
        config = _config(thread_id="t1", checkpoint_id="ckpt-1")
        writes = [("channel_a", "value_a"), ("channel_b", {"key": "val"})]

        await cp.aput_writes(config, writes, task_id="task-1")

        conn = mock_db.pool.acquire.return_value.__aenter__.return_value
        calls = conn.execute.call_args_list
        assert len(calls) == 3  # 1 DELETE + 2 INSERTs
        assert "DELETE FROM graph_writes" in calls[0][0][0]

    @pytest.mark.asyncio
    async def test_pickles_write_values(self, mock_db):
        from src.persistence.checkpoint import PostgresCheckpointer

        cp = PostgresCheckpointer(mock_db)
        config = _config(thread_id="t1", checkpoint_id="ckpt-1")
        writes = [("channel_x", {"nested": "data"})]

        await cp.aput_writes(config, writes, task_id="task-1")

        conn = mock_db.pool.acquire.return_value.__aenter__.return_value
        # args: query, thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, pickle(value)
        insert_args = conn.execute.call_args_list[1][0]
        pickled_value = insert_args[7]
        restored = pickle.loads(pickled_value)
        assert restored == {"nested": "data"}


class TestAgetTuple:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_row(self, mock_db):
        from src.persistence.checkpoint import PostgresCheckpointer

        cp = PostgresCheckpointer(mock_db)
        conn = mock_db.pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow = AsyncMock(return_value=None)

        config = _config(thread_id="t1", checkpoint_id="ckpt-1")
        result = await cp.aget_tuple(config)
        assert result is None

    @pytest.mark.asyncio
    async def test_retrieves_checkpoint_by_id(self, mock_db):
        from src.persistence.checkpoint import PostgresCheckpointer

        cp = PostgresCheckpointer(mock_db)
        ckpt = _checkpoint_data("ckpt-1")
        ckpt["channel_values"] = {"status": "ok"}

        conn = mock_db.pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow = AsyncMock(return_value=_FakeRecord({
            "checkpoint": pickle.dumps(ckpt),
            "metadata": '{"source": "test"}',
            "parent_checkpoint_id": "parent-1",
        }))
        conn.fetch = AsyncMock(return_value=[])

        config = _config(thread_id="t1", checkpoint_id="ckpt-1")
        result = await cp.aget_tuple(config)

        assert result is not None
        assert result.checkpoint["id"] == "ckpt-1"
        assert result.metadata["source"] == "test"
        assert result.parent_config["configurable"]["checkpoint_id"] == "parent-1"

    @pytest.mark.asyncio
    async def test_falls_back_to_latest_when_no_checkpoint_id(self, mock_db):
        from src.persistence.checkpoint import PostgresCheckpointer

        cp = PostgresCheckpointer(mock_db)
        ckpt = _checkpoint_data("latest-ckpt")

        conn = mock_db.pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow = AsyncMock(return_value=_FakeRecord({
            "checkpoint": pickle.dumps(ckpt),
            "metadata": "{}",
            "parent_checkpoint_id": None,
        }))
        conn.fetch = AsyncMock(return_value=[])

        config = _config(thread_id="t1")  # no checkpoint_id
        result = await cp.aget_tuple(config)

        assert result.checkpoint["id"] == "latest-ckpt"
        # Should use ORDER BY created_at DESC LIMIT 1
        sql = conn.fetchrow.call_args[0][0]
        assert "ORDER BY created_at DESC LIMIT 1" in sql

    @pytest.mark.asyncio
    async def test_retrieves_pending_writes(self, mock_db):
        from src.persistence.checkpoint import PostgresCheckpointer

        cp = PostgresCheckpointer(mock_db)
        ckpt = _checkpoint_data("ckpt-1")

        conn = mock_db.pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow = AsyncMock(return_value=_FakeRecord({
            "checkpoint": pickle.dumps(ckpt),
            "metadata": "{}",
            "parent_checkpoint_id": None,
        }))
        conn.fetch = AsyncMock(return_value=[
            _FakeRecord({"task_id": "t1", "channel": "ch1", "value": pickle.dumps("val1")}),
            _FakeRecord({"task_id": "t1", "channel": "ch2", "value": pickle.dumps("val2")}),
        ])

        config = _config(thread_id="t1", checkpoint_id="ckpt-1")
        result = await cp.aget_tuple(config)

        assert result.pending_writes is not None
        assert len(result.pending_writes) == 2

    @pytest.mark.asyncio
    async def test_no_pending_writes_returns_none(self, mock_db):
        from src.persistence.checkpoint import PostgresCheckpointer

        cp = PostgresCheckpointer(mock_db)
        ckpt = _checkpoint_data("ckpt-1")

        conn = mock_db.pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow = AsyncMock(return_value=_FakeRecord({
            "checkpoint": pickle.dumps(ckpt),
            "metadata": "{}",
            "parent_checkpoint_id": None,
        }))
        conn.fetch = AsyncMock(return_value=[])  # no pending writes

        config = _config(thread_id="t1", checkpoint_id="ckpt-1")
        result = await cp.aget_tuple(config)

        assert result.pending_writes is None


class TestAlist:
    @pytest.mark.asyncio
    async def test_yields_checkpoint_tuples(self, mock_db):
        from src.persistence.checkpoint import PostgresCheckpointer

        cp = PostgresCheckpointer(mock_db)
        ckpt = _checkpoint_data("ckpt-1")

        conn = mock_db.pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(return_value=[
            _FakeRecord({
                "checkpoint": pickle.dumps(ckpt),
                "metadata": "{}",
                "checkpoint_id": "ckpt-1",
                "parent_checkpoint_id": None,
            }),
        ])

        config = _config(thread_id="t1")
        results = [item async for item in cp.alist(config)]

        assert len(results) == 1
        assert results[0].checkpoint["id"] == "ckpt-1"

    @pytest.mark.asyncio
    async def test_respects_limit(self, mock_db):
        from src.persistence.checkpoint import PostgresCheckpointer

        cp = PostgresCheckpointer(mock_db)
        conn = mock_db.pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(return_value=[])

        config = _config(thread_id="t1")
        _ = [item async for item in cp.alist(config, limit=5)]

        sql = conn.fetch.call_args[0][0]
        assert "LIMIT $3" in sql


class TestAdeleteThread:
    @pytest.mark.asyncio
    async def test_deletes_writes_and_checkpoints(self, mock_db):
        from src.persistence.checkpoint import PostgresCheckpointer

        cp = PostgresCheckpointer(mock_db)
        await cp.adelete_thread("thread-to-delete")

        conn = mock_db.pool.acquire.return_value.__aenter__.return_value
        assert conn.execute.call_count == 2
        delete_calls = [c[0][0] for c in conn.execute.call_args_list]
        assert any("graph_writes" in sql for sql in delete_calls)
        assert any("graph_checkpoints" in sql for sql in delete_calls)


class _FakeRecord:
    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)

    def keys(self):
        return self._data.keys()
