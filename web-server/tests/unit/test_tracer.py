import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit


class TestEstimateTokens:
    def test_aggregates_multiple_messages(self):
        from src.observability.tracer import _estimate_tokens

        tokens = _estimate_tokens([
            {"content": "aaaa"},
            {"content": "bbbb"},
        ])
        assert tokens == 2  # 8 chars / 4


class TestPostgresTracer:
    @pytest.mark.asyncio
    async def test_trace_inserts_row(self):
        from src.observability.tracer import PostgresTracer

        db = MagicMock()
        db.execute = AsyncMock()

        tracer = PostgresTracer(db)
        await tracer.trace_llm_call(
            chat_id=uuid.uuid4(),
            model="gpt-4",
            messages=[{"role": "user", "content": "hello"}],
            response={"content": "hi there"},
            latency_ms=150,
        )

        db.execute.assert_called_once()
        args = db.execute.call_args[0]
        assert args[0].startswith("INSERT INTO llm_traces")

    @pytest.mark.asyncio
    async def test_trace_includes_tool_calls_when_content_present(self):
        from src.observability.tracer import PostgresTracer

        db = MagicMock()
        db.execute = AsyncMock()

        tracer = PostgresTracer(db)
        await tracer.trace_llm_call(
            chat_id=uuid.uuid4(),
            model="gpt-4",
            messages=[],
            response={"content": "result", "tool_calls": [{"name": "get_cpu"}]},
            latency_ms=50,
        )

        args = db.execute.call_args[0]
        import json
        completion_text = args[5]  # index 5: completion_text ($5 in SQL)
        parsed = json.loads(completion_text)
        assert "tool_calls" in parsed
        assert parsed["tool_calls"] == [{"name": "get_cpu"}]

    @pytest.mark.asyncio
    async def test_trace_empty_content_sets_completion_none(self):
        """Empty content with no tool_calls → nothing to save → None."""
        from src.observability.tracer import PostgresTracer

        db = MagicMock()
        db.execute = AsyncMock()

        tracer = PostgresTracer(db)
        await tracer.trace_llm_call(
            chat_id=uuid.uuid4(),
            model="gpt-4",
            messages=[],
            response={"content": ""},
            latency_ms=50,
        )

        args = db.execute.call_args[0]
        completion_text = args[5]
        assert completion_text is None

    @pytest.mark.asyncio
    async def test_trace_empty_content_with_tool_calls_saved(self):
        """Fix: empty content but has tool_calls → completion_text is saved."""
        from src.observability.tracer import PostgresTracer

        db = MagicMock()
        db.execute = AsyncMock()

        tracer = PostgresTracer(db)
        await tracer.trace_llm_call(
            chat_id=uuid.uuid4(),
            model="gpt-4",
            messages=[],
            response={"content": "", "tool_calls": [{"name": "get_cpu"}]},
            latency_ms=50,
        )

        args = db.execute.call_args[0]
        import json
        completion_text = args[5]
        parsed = json.loads(completion_text)
        assert parsed["content"] == ""
        assert parsed["tool_calls"] == [{"name": "get_cpu"}]

    @pytest.mark.asyncio
    async def test_trace_uses_correct_chat_id(self):
        from src.observability.tracer import PostgresTracer

        db = MagicMock()
        db.execute = AsyncMock()

        tracer = PostgresTracer(db)
        cid = uuid.uuid4()
        await tracer.trace_llm_call(
            chat_id=cid,
            model="gpt-4",
            messages=[],
            response={"content": "x"},
            latency_ms=10,
        )

        args = db.execute.call_args[0]
        assert args[2] == cid  # chat_id is 3rd positional arg ($3)
