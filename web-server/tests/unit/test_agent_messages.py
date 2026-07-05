import json

import pytest

from src.agent.messages import normalize_message

pytestmark = pytest.mark.unit


class TestNormalizeMessage:
    def test_normalizes_dict_message_with_tool_calls(self):
        msg = normalize_message(
            {
                "type": "ai",
                "content": "checking",
                "tool_calls": [
                    {
                        "id": "tc-1",
                        "name": "get_cpu",
                        "args": {"detail": True},
                    }
                ],
                "reasoning_content": "need metrics",
            }
        )

        assert msg == {
            "role": "assistant",
            "content": "checking",
            "tool_calls": [
                {
                    "id": "tc-1",
                    "type": "function",
                    "function": {
                        "name": "get_cpu",
                        "arguments": json.dumps({"detail": True}),
                    },
                }
            ],
            "reasoning_content": "need metrics",
        }

    def test_normalizes_object_message_with_tool_metadata(self):
        class ToolCall:
            id = "tc-1"
            name = "get_cpu"
            args = {"detail": True}

        class Message:
            type = "tool"
            content = "CPU: 45%"
            tool_call_id = "tc-1"
            name = "get_cpu"
            additional_kwargs = {"reasoning_content": "ignored for tool"}
            tool_calls = [ToolCall()]

        msg = normalize_message(Message())

        assert msg["role"] == "tool"
        assert msg["content"] == "CPU: 45%"
        assert msg["tool_call_id"] == "tc-1"
        assert msg["name"] == "get_cpu"
        assert msg["reasoning_content"] == "ignored for tool"
        assert msg["tool_calls"][0]["function"]["name"] == "get_cpu"

    def test_object_message_handles_none_additional_kwargs(self):
        class Message:
            type = "human"
            content = "hello"
            additional_kwargs = None

        assert normalize_message(Message()) == {"role": "user", "content": "hello"}

    def test_preserves_openai_tool_call_shape(self):
        tool_call = {
            "id": "tc-openai",
            "type": "function",
            "function": {"name": "bash", "arguments": '{"cmd":"ls"}'},
        }

        msg = normalize_message(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [tool_call],
            }
        )

        assert msg["tool_calls"] == [tool_call]
