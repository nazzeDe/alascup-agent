import re

import pytest

pytestmark = pytest.mark.unit

# Regex that matches the OpenAI API tool name constraint
_VALID_TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]+$")


class TestFormatToolsContract:
    """Verify _format_tools produces valid OpenAI-compatible tool schemas."""

    def test_tool_names_match_openai_pattern(self):
        """OpenAI API rejects names with /, spaces, or special chars."""
        from src.agent.nodes import _format_tools

        tools = [
            {"name": "get_cpu_info", "server_name": "tool-server",
             "description": "获取 CPU 信息"},
            {"name": "run_bash", "server_name": "tool-server",
             "description": "执行 bash 命令"},
        ]

        result = _format_tools(tools)

        for t in result:
            name = t["function"]["name"]
            assert _VALID_TOOL_NAME.match(name), (
                f"Tool name {name!r} does not match OpenAI pattern {_VALID_TOOL_NAME.pattern}"
            )

    def test_tool_parameters_has_json_schema_type_object(self):
        """DeepSeek API rejects empty parameters {} — requires type: object."""
        from src.agent.nodes import _format_tools

        tools = [
            {"name": "no_schema_tool", "server_name": "srv",
             "description": "tool without params_schema"},
        ]

        result = _format_tools(tools)

        for t in result:
            params = t["function"]["parameters"]
            assert isinstance(params, dict), f"parameters must be dict, got {type(params)}"
            assert params.get("type") == "object", (
                f"parameters must have type: object, got {params}"
            )

    def test_valid_params_schema_preserved(self):
        """When a tool provides a valid JSON Schema, it should pass through."""
        from src.agent.nodes import _format_tools

        schema = {"type": "object", "properties": {"path": {"type": "string"}}}
        tools = [
            {"name": "read_logs", "server_name": "tool-server",
             "description": "read logs", "params_schema": schema},
        ]

        result = _format_tools(tools)

        assert result[0]["function"]["parameters"] == schema


class TestMessagesContract:
    """Verify _messages produces OpenAI-compatible roles."""

    def test_roles_are_openai_compatible(self):
        """LangGraph message roles (human, ai) must be mapped to OpenAI roles."""
        from src.agent.nodes import _messages

        from langgraph.graph import MessagesState

        state = {
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
            ],
        }

        result = _messages(state)

        allowed = {"user", "assistant", "tool", "system"}
        for m in result:
            assert m["role"] in allowed, (
                f"Role {m['role']!r} not in OpenAI-compatible roles {allowed}"
            )

    def test_langgraph_human_mapped_to_user(self):
        """When LangGraph converts dicts to HumanMessage, type=human → role=user."""
        from src.agent.nodes import _messages
        from langchain_core.messages import HumanMessage, AIMessage

        state = {
            "messages": [
                HumanMessage(content="check CPU"),
                AIMessage(content="OK"),
            ],
        }

        result = _messages(state)

        assert result[0]["role"] == "user", f"expected user, got {result[0]['role']}"
        assert result[1]["role"] == "assistant", f"expected assistant, got {result[1]['role']}"
