import json

from src.services.llm_stream_assembly import LLMStreamAssembly


def test_llm_stream_assembly_preserves_deltas_and_merges_tool_chunks():
    assembly = LLMStreamAssembly()

    outputs = []
    outputs.extend(assembly.process({
        "event": "assistant",
        "data": json.dumps({"reasoning_content": "think ", "delta": "I will "}),
    }))
    outputs.extend(assembly.process({
        "event": "tool_call",
        "data": json.dumps({"id": "tc-1", "function": {"name": "bash", "arguments": ""}}),
    }))
    outputs.extend(assembly.process({
        "event": "tool_call",
        "data": json.dumps({"function": {"arguments": '{"cmd":"ls"}'}}),
    }))
    outputs.extend(assembly.process({"event": "done", "data": "{}"}))

    assert outputs == [("reasoning", "think "), ("assistant", "I will ")]
    assert assembly.assistant_message() == {
        "role": "assistant",
        "content": "I will ",
        "reasoning_content": "think ",
        "tool_calls": [
            {
                "id": "tc-1",
                "type": "function",
                "function": {"name": "bash", "arguments": '{"cmd":"ls"}'},
            }
        ],
    }
    assert assembly.done is True


def test_llm_stream_assembly_preserves_explicit_tool_call_id():
    assembly = LLMStreamAssembly()

    assembly.process({
        "event": "tool_call",
        "data": json.dumps({"id": "explicit-id", "function": {"name": "get_cpu", "arguments": "{}"}}),
    })

    assert assembly.tool_calls[0]["id"] == "explicit-id"


def test_llm_stream_assembly_creates_function_block_from_arguments_only_chunk():
    assembly = LLMStreamAssembly()

    assembly.process({
        "event": "tool_call",
        "data": json.dumps({"function": {"name": "", "arguments": '{"cmd":"ls"}'}}),
    })

    assert len(assembly.tool_calls) == 1
    assert assembly.tool_calls[0]["type"] == "function"
    assert assembly.tool_calls[0]["function"] == {"name": "", "arguments": '{"cmd":"ls"}'}
