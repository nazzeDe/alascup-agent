import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def llm_config():
    from src.config.models import LLMConfig

    return LLMConfig(api_key="sk-test", api_url="https://api.example.com/v1", model="test-model")


def _mock_response(json_data: dict, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data
    resp.text = json.dumps(json_data)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=None)
    return resp


def _mock_stream_response(chunks: list[str], status: int = 200) -> MagicMock:
    async def async_chunks():
        for c in chunks:
            yield c

    resp = MagicMock()
    resp.status_code = status
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=None)
    resp.aiter_lines.return_value = async_chunks()
    resp.aread = AsyncMock(return_value=b"error")
    return resp


class TestLLMAdapterGenerate:
    @pytest.mark.asyncio
    async def test_generate_returns_content(self, llm_config):
        from src.services.llm_adapter import LLMAdapter

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=_mock_response({
            "choices": [{"message": {"content": "Hello, world!", "tool_calls": None}}],
        }))

        adapter = LLMAdapter(llm_config)
        adapter._client = lambda: mock_client

        result = await adapter.generate([{"role": "user", "content": "hi"}])

        assert result["content"] == "Hello, world!"
        assert result["tool_calls"] is None

    @pytest.mark.asyncio
    async def test_generate_returns_tool_calls(self, llm_config):
        from src.services.llm_adapter import LLMAdapter

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=_mock_response({
            "choices": [{"message": {"content": "", "tool_calls": [
                {"function": {"name": "get_cpu_info", "arguments": "{}"}},
            ]}}],
        }))

        adapter = LLMAdapter(llm_config)
        adapter._client = lambda: mock_client

        result = await adapter.generate([{"role": "user", "content": "check cpu"}])

        assert result["tool_calls"] is not None
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["function"]["name"] == "get_cpu_info"

    @pytest.mark.asyncio
    async def test_generate_sends_system_prompt(self, llm_config):
        from src.services.llm_adapter import LLMAdapter

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=_mock_response({
            "choices": [{"message": {"content": "ok", "tool_calls": None}}],
        }))

        adapter = LLMAdapter(llm_config)
        adapter._client = lambda: mock_client

        await adapter.generate([{"role": "user", "content": "hi"}], system="You are helpful.")

        call_args = mock_client.post.call_args
        body = call_args.kwargs["json"]
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][0]["content"] == "You are helpful."

    @pytest.mark.asyncio
    async def test_generate_includes_tools(self, llm_config):
        from src.services.llm_adapter import LLMAdapter

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=_mock_response({
            "choices": [{"message": {"content": "ok", "tool_calls": None}}],
        }))

        adapter = LLMAdapter(llm_config)
        adapter._client = lambda: mock_client

        tools = [{"type": "function", "function": {"name": "get_cpu_info", "parameters": {}}}]
        await adapter.generate([{"role": "user", "content": "hi"}], tools=tools)

        call_args = mock_client.post.call_args
        body = call_args.kwargs["json"]
        assert body["tools"] == tools

    @pytest.mark.asyncio
    async def test_generate_raises_on_http_error(self, llm_config):
        from src.services.llm_adapter import LLMAdapter

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=_mock_response(
            {"error": {"message": "internal error"}}, status=500
        ))

        adapter = LLMAdapter(llm_config)
        adapter._client = lambda: mock_client

        with pytest.raises(RuntimeError, match="LLM API error"):
            await adapter.generate([{"role": "user", "content": "hi"}])


class TestLLMAdapterGenerateStream:
    @pytest.mark.asyncio
    async def test_generate_stream_yields_deltas(self, llm_config):
        from src.services.llm_adapter import LLMAdapter

        chunks = [
            'data: {"choices":[{"delta":{"content":"Hello"}}]}',
            'data: {"choices":[{"delta":{"content":" world"}}]}',
            'data: {"choices":[{"delta":{"content":"!"}}]}',
            'data: [DONE]',
        ]
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mock_client.stream = MagicMock(return_value=_mock_stream_response(chunks))

        adapter = LLMAdapter(llm_config)
        adapter._client = lambda: mock_client

        parts = []
        async for event in adapter.generate_stream([{"role": "user", "content": "hi"}]):
            parts.append(event)

        assert any("Hello" in p.get("data", "") for p in parts if p.get("event") == "assistant")
        assert any(p["event"] == "done" for p in parts)

    @pytest.mark.asyncio
    async def test_generate_stream_yields_tool_calls(self, llm_config):
        from src.services.llm_adapter import LLMAdapter

        chunks = [
            'data: {"choices":[{"delta":{"content":"","tool_calls":[{"index":0,"function":{"name":"get_cpu_info","arguments":"{}"}}]}}]}',
            'data: [DONE]',
        ]
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.stream = MagicMock(return_value=_mock_stream_response(chunks))

        adapter = LLMAdapter(llm_config)
        adapter._client = lambda: mock_client

        events = []
        async for event in adapter.generate_stream([{"role": "user", "content": "check"}]):
            events.append(event)

        tool_call_events = [e for e in events if e.get("event") == "tool_call"]
        done_events = [e for e in events if e.get("event") == "done"]
        assert len(tool_call_events) >= 1
        assert len(done_events) >= 1
        # tool_calls must be yielded BEFORE done — otherwise think_node breaks too early
        last_tc_idx = max(events.index(e) for e in tool_call_events)
        first_done_idx = min(events.index(e) for e in done_events)
        assert last_tc_idx < first_done_idx, "tool_call events must come BEFORE done event"

    @pytest.mark.asyncio
    async def test_generate_stream_tool_calls_before_done(self, llm_config):
        """Multi-chunk tool call deltas: tool_calls must appear before done."""
        from src.services.llm_adapter import LLMAdapter

        chunks = [
            'data: {"choices":[{"delta":{"content":null,"tool_calls":[{"index":0,"function":{"name":"get_cpu_info","arguments":""}}]}}]}',
            'data: {"choices":[{"delta":{"content":null,"tool_calls":[{"index":0,"function":{"arguments":"{}"}}]}}]}',
            'data: [DONE]',
        ]
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.stream = MagicMock(return_value=_mock_stream_response(chunks))

        adapter = LLMAdapter(llm_config)
        adapter._client = lambda: mock_client

        events = []
        async for event in adapter.generate_stream([{"role": "user", "content": "check"}]):
            events.append(event)

        event_types = [e["event"] for e in events]
        tc_indices = [i for i, t in enumerate(event_types) if t == "tool_call"]
        done_index = next(i for i, t in enumerate(event_types) if t == "done")

        assert tc_indices, "expected at least one tool_call event"
        assert all(i < done_index for i in tc_indices), (
            f"tool_calls at indices {tc_indices} must all be before done at {done_index}"
        )

    @pytest.mark.asyncio
    async def test_generate_stream_with_system_prompt(self, llm_config):
        from src.services.llm_adapter import LLMAdapter

        chunks = [
            'data: {"choices":[{"delta":{"content":"ok"}}]}',
            'data: [DONE]',
        ]
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.stream = MagicMock(return_value=_mock_stream_response(chunks))

        adapter = LLMAdapter(llm_config)
        adapter._client = lambda: mock_client

        found = False
        async for event in adapter.generate_stream(
            [{"role": "user", "content": "hi"}], system="You are helpful."
        ):
            if event.get("event") == "assistant":
                assert "ok" in event["data"]
                found = True
        assert found

    @pytest.mark.asyncio
    async def test_generate_stream_handles_error_event(self, llm_config):
        from src.services.llm_adapter import LLMAdapter

        chunks: list[str] = []
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.stream = MagicMock(return_value=_mock_stream_response(chunks, status=500))

        adapter = LLMAdapter(llm_config)
        adapter._client = lambda: mock_client

        events = []
        async for event in adapter.generate_stream([{"role": "user", "content": "hi"}]):
            events.append(event)

        assert any(e["event"] == "error" for e in events)
