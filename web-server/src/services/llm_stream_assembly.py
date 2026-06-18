import json
from uuid import uuid4


class LLMStreamAssembly:
    """Assembles provider stream events into assistant message state."""

    def __init__(self) -> None:
        self._text: list[str] = []
        self._reasoning: list[str] = []
        self._tool_calls: list[dict] = []
        self.done = False

    def process(self, event: dict) -> list[tuple[str, str]]:
        if not isinstance(event, dict) or "event" not in event:
            raise ValueError(f"Invalid stream event — expected dict with 'event' key: {type(event).__name__}")

        event_type = event["event"]
        if event_type == "assistant":
            return self._process_assistant(event)
        if event_type == "tool_call":
            self._merge_tool_block(json.loads(event["data"]))
            return []
        if event_type == "error":
            raise LLMStreamError(json.loads(event["data"]))
        if event_type == "done":
            self.done = True
            return []
        return []

    def assistant_message(self) -> dict | None:
        text = "".join(self._text)
        reasoning = "".join(self._reasoning)
        if not (text or reasoning or self._tool_calls):
            return None
        msg: dict = {"role": "assistant", "content": text or ""}
        if reasoning:
            msg["reasoning_content"] = reasoning
        if self._tool_calls:
            msg["tool_calls"] = self.tool_calls
        return msg

    @property
    def tool_calls(self) -> list[dict]:
        return self._tool_calls

    def _process_assistant(self, event: dict) -> list[tuple[str, str]]:
        data = json.loads(event["data"])
        chunks: list[tuple[str, str]] = []
        reasoning = data.get("reasoning_content", "")
        if reasoning:
            self._reasoning.append(reasoning)
            chunks.append(("reasoning", reasoning))
        delta = data.get("delta", "")
        if delta:
            self._text.append(delta)
            chunks.append(("assistant", delta))
        return chunks

    def _merge_tool_block(self, chunk: dict) -> None:
        fn = chunk.get("function", {})
        name = fn.get("name", "")
        args_chunk = fn.get("arguments", "")

        if name:
            self._tool_calls.append({
                "id": chunk.get("id") or str(uuid4()),
                "type": "function",
                "function": {"name": name, "arguments": args_chunk},
            })
        elif args_chunk and self._tool_calls:
            self._tool_calls[-1]["function"]["arguments"] += args_chunk
        elif args_chunk:
            self._tool_calls.append({
                "id": chunk.get("id") or str(uuid4()),
                "type": "function",
                "function": {"name": "", "arguments": args_chunk},
            })


class LLMStreamError(Exception):
    def __init__(self, error: dict) -> None:
        super().__init__(error.get("message", "LLM stream error"))
        self.error = error
