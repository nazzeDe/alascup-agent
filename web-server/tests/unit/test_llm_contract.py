"""Validate that ALL requests sent to DeepSeek API comply with the format
requirements documented in doc/api-spec/deepseek-api.md.

These tests intercept the actual HTTP payload built by LLMAdapter and assert
structural invariants — they do NOT test internal message conversion.
"""

import json
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit

_VALID_TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]+$")
_OPENAI_ROLES = {"system", "user", "assistant", "tool"}


# ── helpers ────────────────────────────────────────────────────────────────

def _make_adapter(max_tokens=393216):
    from src.services.llm_adapter import LLMAdapter
    from src.config.models import LLMConfig

    config = LLMConfig(
        api_key="sk-test", api_url="https://api.deepseek.com",
        model="deepseek-v4-flash", max_tokens=max_tokens,
    )
    return LLMAdapter(config)


def _capture_stream_payload(adapter, messages, tools=None, system=None):
    """Run generate_stream and capture the JSON payload sent to the API."""
    captured = {}

    async def _run():
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        async def fake_stream(method, url, json=None):
            captured["payload"] = json
            resp = MagicMock()
            resp.status_code = 200
            resp.__aenter__ = AsyncMock(return_value=resp)
            resp.__aexit__ = AsyncMock(return_value=None)

            async def lines():
                yield 'data: {"choices":[{"delta":{"content":"ok"}}]}'
                yield "data: [DONE]"

            resp.aiter_lines = lines
            return resp

        mock_client.stream = fake_stream
        adapter._streaming_client = lambda: mock_client

        async for _ in adapter.generate_stream(messages, tools=tools, system=system):
            pass

    import asyncio
    asyncio.get_event_loop().run_until_complete(_run())
    return captured.get("payload", {})


# ── Payload structure ──────────────────────────────────────────────────────

class TestPayloadContract:
    """The JSON body sent to POST /chat/completions must be valid."""

    def test_required_fields_present(self):
        payload = _make_adapter()._build_payload(
            [{"role": "user", "content": "hi"}], None, None, False,
        )
        for field in ("model", "messages", "stream", "max_tokens"):
            assert field in payload, f"missing required field: {field}"

    def test_max_tokens_in_valid_range(self):
        payload = _make_adapter()._build_payload(
            [{"role": "user", "content": "hi"}], None, None, False,
        )
        assert 1 <= payload["max_tokens"] <= 393216

    def test_tools_absent_when_none(self):
        payload = _make_adapter()._build_payload(
            [{"role": "user", "content": "hi"}], None, None, False,
        )
        assert "tools" not in payload

    def test_tools_present_when_provided(self):
        tools = [{"type": "function", "function": {"name": "foo", "parameters": {"type": "object"}}}]
        payload = _make_adapter()._build_payload(
            [{"role": "user", "content": "hi"}], tools, None, False,
        )
        assert payload["tools"] == tools


# ── Message format in outgoing payload ─────────────────────────────────────

class TestOutgoingMessageFormat:
    """Messages in the payload must comply with DeepSeek/OpenAI format."""

    def test_all_roles_valid(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello", "reasoning_content": "thinking"},
            {"role": "tool", "content": "result", "tool_call_id": "c1", "name": "foo"},
        ]
        payload = _make_adapter()._build_payload(messages, None, "sys", False)
        for m in payload["messages"]:
            assert m["role"] in _OPENAI_ROLES, f"invalid role: {m['role']}"

    def test_assistant_with_reasoning_and_tool_calls(self):
        """Multi-turn with tool results: assistant msg must preserve reasoning_content."""
        messages = [
            {"role": "user", "content": "查看负载"},
            {
                "role": "assistant", "content": "好的",
                "reasoning_content": "用户要看负载",
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "get_cpu", "arguments": "{}"}}],
            },
            {"role": "tool", "content": '{"cpu": 50}', "tool_call_id": "c1", "name": "get_cpu"},
        ]
        payload = _make_adapter()._build_payload(messages, None, None, False)

        assistant = payload["messages"][1]
        assert "reasoning_content" in assistant, (
            "DeepSeek rejects requests missing reasoning_content"
        )
        assert assistant["reasoning_content"] == "用户要看负载"

    def test_tool_calls_arguments_is_string(self):
        """arguments must be a JSON string, never a raw dict."""
        messages = [
            {"role": "user", "content": "test"},
            {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "foo", "arguments": '{"cmd":"ls"}'}}],
            },
        ]
        payload = _make_adapter()._build_payload(messages, None, None, False)

        tc = payload["messages"][1]["tool_calls"][0]
        assert isinstance(tc["function"]["arguments"], str)
        json.loads(tc["function"]["arguments"])  # must be valid JSON

    def test_tool_message_has_tool_call_id(self):
        messages = [
            {"role": "user", "content": "test"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "foo", "arguments": "{}"}}]},
            {"role": "tool", "content": "result", "tool_call_id": "c1", "name": "foo"},
        ]
        payload = _make_adapter()._build_payload(messages, None, None, False)

        tool_msg = payload["messages"][2]
        assert "tool_call_id" in tool_msg
        assert tool_msg["tool_call_id"] == "c1"

    def test_system_message_first(self):
        payload = _make_adapter()._build_payload(
            [{"role": "user", "content": "hi"}], None, "system prompt", False,
        )
        assert payload["messages"][0] == {"role": "system", "content": "system prompt"}


# ── Streaming payload ──────────────────────────────────────────────────────

class TestStreamingPayload:
    """generate_stream must send valid payloads."""

    def test_stream_flag_true(self):
        payload = _make_adapter()._build_payload(
            [{"role": "user", "content": "hi"}], None, None, True,
        )
        assert payload["stream"] is True

    def test_stream_with_multi_turn_preserves_reasoning(self):
        """Streaming request with tool results must include reasoning_content."""
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "ok", "reasoning_content": "thinking",
             "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "foo", "arguments": "{}"}}]},
            {"role": "tool", "content": "data", "tool_call_id": "c1", "name": "foo"},
        ]
        payload = _make_adapter()._build_payload(messages, None, None, True)

        assert payload["messages"][1]["reasoning_content"] == "thinking"


# ── Tool schema format ─────────────────────────────────────────────────────

class TestToolSchemaFormat:
    """Tools definition must comply with OpenAI function calling format."""

    def test_tool_names_valid(self):
        from src.agent.nodes import _format_tools

        tools = [
            {"name": "get_cpu_info", "server_name": "tool-server", "description": "desc"},
            {"name": "run_bash", "server_name": "tool-server", "description": "desc"},
        ]
        result = _format_tools(tools)
        for t in result:
            name = t["function"]["name"]
            assert _VALID_TOOL_NAME.match(name), f"invalid tool name: {name}"

    def test_parameters_must_be_json_schema_object(self):
        from src.agent.nodes import _format_tools

        tools = [{"name": "no_schema", "server_name": "srv", "description": "desc"}]
        result = _format_tools(tools)

        params = result[0]["function"]["parameters"]
        assert params.get("type") == "object"

    def test_tool_type_is_function(self):
        from src.agent.nodes import _format_tools

        tools = [{"name": "foo", "server_name": "srv", "description": "desc"}]
        result = _format_tools(tools)
        assert result[0]["type"] == "function"
