import json
import time
import uuid as _uuid
from dataclasses import dataclass, field
from typing import AsyncIterator
from uuid import UUID, uuid4

from httpx import AsyncClient, Timeout

from src.config.models import LLMConfig


_STATUS_ERROR_MAP: dict[int, str] = {
    429: "rate_limit",
    401: "auth_failed",
    403: "auth_failed",
    404: "model_unavailable",
    500: "server_error",
    502: "server_error",
    503: "server_error",
    529: "server_error",
}

DEFAULT_TIMEOUT_SECONDS = 120
STREAM_TIMEOUT_SECONDS = 30
CHAT_COMPLETIONS_PATH = "/chat/completions"
SUMMARY_MAX_TOKENS = 1024
SUMMARY_SYSTEM_PROMPT = (
    "Summarize the following conversation concisely, preserving key facts and decisions."
)

WireEvent = dict[str, str]
Payload = dict


def classify_error(
    status_code: int, response_text: str, stop_reason: str | None = None
) -> str | None:
    """Classify LLM API errors into standardized error types."""
    if status_code == 200:
        return "max_output_tokens" if stop_reason == "max_tokens" else None

    if status_code in (413, 400):
        return _classify_bad_request(response_text)

    if mapped := _STATUS_ERROR_MAP.get(status_code):
        return mapped

    return _classify_status_code(status_code)


def _classify_bad_request(response_text: str) -> str:
    lower = response_text.lower()
    if "max_tokens" in lower or "invalid" in lower:
        return "unknown"
    if any(kw in lower for kw in ("too large", "too long", "context", "token")):
        return "prompt_too_long"
    # All other 400/413 errors (message format, content filter, etc.)
    # → treat as compressible to attempt recovery
    return "prompt_too_long"


def _classify_status_code(status_code: int) -> str | None:
    if status_code == 0:
        return "timeout"
    if status_code >= 500:
        return "server_error"
    if status_code >= 400:
        return "unknown"
    return None


@dataclass
class StreamState:
    """Accumulated state from one streaming LLM response."""

    tool_calls: dict[int, dict] = field(default_factory=dict)
    content: str = ""
    usage: dict | None = None

    def record(self, event: WireEvent) -> None:
        if event.get("event") == "assistant":
            self.content += _extract_delta(event)

    def response(self) -> dict:
        response: dict = {"content": self.content}
        if self.tool_calls:
            response["tool_calls"] = list(self.tool_calls.values())
        return response


class PayloadBuilder:
    """Build OpenAI-compatible chat completion payloads."""

    def __init__(self, config: LLMConfig, max_tokens: int) -> None:
        self._config = config
        self._max_tokens = max_tokens

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        system: str | None,
        *,
        stream: bool,
    ) -> Payload:
        msgs = self._with_system_message(messages, system)
        payload: Payload = {
            "model": self._config.model,
            "messages": msgs,
            "stream": stream,
            "max_tokens": self._max_tokens,
        }
        if stream:
            payload["stream_options"] = {"include_usage": True}
        if tools:
            payload["tools"] = tools
        return payload

    def summary(self, messages: list[dict]) -> Payload:
        model = self._config.summary_model or self._config.model
        return {
            "model": model,
            "messages": [
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": "\n".join(m.get("content", "") for m in messages)},
            ],
            "stream": False,
            "max_tokens": SUMMARY_MAX_TOKENS,
        }

    @staticmethod
    def _with_system_message(messages: list[dict], system: str | None) -> list[dict]:
        if not system:
            return list(messages)
        return [{"role": "system", "content": system}, *messages]


class StreamParser:
    """Parse OpenAI-compatible SSE data chunks into internal wire events."""

    @staticmethod
    def parse(data_str: str, state: StreamState) -> list[WireEvent]:
        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            return []

        StreamParser._capture_usage(chunk, state)
        delta = chunk.get("choices", [{}])[0].get("delta", {})
        tool_calls = delta.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                _accumulate_tool_call(tc, state.tool_calls)

        return StreamParser._assistant_events(delta)

    @staticmethod
    def _capture_usage(chunk: dict, state: StreamState) -> None:
        if "usage" in chunk:
            state.usage = chunk["usage"]

    @staticmethod
    def _assistant_events(delta: dict) -> list[WireEvent]:
        content = delta.get("content", "")
        reasoning = delta.get("reasoning_content", "")
        if not (content or reasoning):
            return []

        data: dict[str, str] = {}
        if content:
            data["delta"] = content
        if reasoning:
            data["reasoning_content"] = reasoning
        return [{"event": "assistant", "data": json.dumps(data)}]


def _accumulate_tool_call(tc: dict, accumulated: dict[int, dict]) -> None:
    """Merge a streaming tool_call chunk into the accumulated dict."""
    idx = tc.get("index", 0)
    if idx not in accumulated:
        accumulated[idx] = {
            "id": tc.get("id") or str(uuid4()),
            "function": {"name": "", "arguments": ""},
        }
    else:
        tid = tc.get("id", "")
        if tid and not accumulated[idx].get("id"):
            accumulated[idx]["id"] = tid
    fn = tc.get("function", {})
    if "name" in fn:
        accumulated[idx]["function"]["name"] += fn["name"]
    if "arguments" in fn:
        accumulated[idx]["function"]["arguments"] += fn["arguments"]


def _extract_delta(event: dict) -> str:
    """Extract delta text from an assistant SSE event."""
    try:
        return json.loads(event["data"]).get("delta", "")
    except (json.JSONDecodeError, KeyError):
        return ""


def _auth_headers(config: LLMConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }


def _error_event(status_code: int, message: str) -> WireEvent:
    return {
        "event": "error",
        "data": json.dumps({"code": status_code, "message": message}),
    }


def _chat_result(data: dict) -> dict:
    choice = data["choices"][0]["message"]
    return {
        "content": choice.get("content", ""),
        "tool_calls": choice.get("tool_calls"),
    }


class LLMAdapter:
    def __init__(self, config: LLMConfig, transport=None, tracer=None) -> None:
        self._config = config
        self._transport = transport
        self._tracer = tracer
        self._max_tokens = config.max_tokens
        self._last_trace_id: UUID | None = None

    def escalate_max_tokens(self) -> None:
        """EH-004 恢复：翻倍 max_tokens 上限。"""
        self._max_tokens *= 2

    def switch_to_fallback(self) -> None:
        """EH-005 恢复：切换到 fallback_model。"""
        if self._config.fallback_model:
            self._config.model = self._config.fallback_model

    def _client(self) -> AsyncClient:
        return AsyncClient(
            base_url=self._config.api_url,
            headers=_auth_headers(self._config),
            timeout=DEFAULT_TIMEOUT_SECONDS,
            transport=self._transport,
        )

    def _streaming_client(self) -> AsyncClient:
        """Client with tighter read timeout for streaming (30s between chunks)."""
        return AsyncClient(
            base_url=self._config.api_url,
            headers=_auth_headers(self._config),
            timeout=Timeout(
                connect=STREAM_TIMEOUT_SECONDS,
                read=STREAM_TIMEOUT_SECONDS,
                write=STREAM_TIMEOUT_SECONDS,
                pool=STREAM_TIMEOUT_SECONDS,
            ),
            transport=self._transport,
        )

    async def generate(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
        chat_id: UUID | None = None,
    ) -> dict:
        start = time.monotonic()
        payload = self._build_payload(messages, tools, system, stream=False)
        async with self._client() as client:
            resp = await client.post(CHAT_COMPLETIONS_PATH, json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"LLM API error: {resp.status_code} {resp.text}")
            data = resp.json()
        latency_ms = int((time.monotonic() - start) * 1000)
        result = _chat_result(data)
        await self._trace_call(chat_id, messages, result, latency_ms, data.get("usage"))
        return result

    async def generate_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
        chat_id: UUID | None = None,
    ) -> AsyncIterator[WireEvent]:
        start = time.monotonic()
        payload = self._build_payload(messages, tools, system, stream=True)
        state = StreamState()

        async with self._streaming_client() as client:
            async with client.stream("POST", CHAT_COMPLETIONS_PATH, json=payload) as resp:
                if resp.status_code != 200:
                    text = await resp.aread()
                    yield _error_event(resp.status_code, text.decode())
                    return

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        await self._trace_stream(chat_id, messages, state, start)
                        for tc in state.tool_calls.values():
                            yield {"event": "tool_call", "data": json.dumps(tc)}
                        yield {"event": "done", "data": "{}"}
                        break

                    for event in StreamParser.parse(data_str, state):
                        yield event
                        state.record(event)

    async def _trace_stream(
        self,
        chat_id: UUID | None,
        messages: list[dict],
        state: StreamState,
        start: float,
    ) -> None:
        latency_ms = int((time.monotonic() - start) * 1000)
        await self._trace_call(chat_id, messages, state.response(), latency_ms, state.usage)

    async def _trace_call(
        self,
        chat_id: UUID | None,
        messages: list[dict],
        response: dict,
        latency_ms: int,
        usage: dict | None = None,
    ) -> None:
        if not self._tracer:
            return
        self._last_trace_id = await self._tracer.trace_llm_call(
            chat_id=chat_id if chat_id else _uuid.uuid4(),
            model=self._config.model,
            messages=messages,
            response=response,
            latency_ms=latency_ms,
            usage=usage,
        )

    @staticmethod
    def _process_chunk(data_str: str, accumulated: dict[int, dict]):
        """Parse a single SSE data line and yield assistant/tool_call events."""
        state = StreamState(tool_calls=accumulated)
        yield from StreamParser.parse(data_str, state)

    async def summarize(self, messages: list[dict]) -> str:
        payload = PayloadBuilder(self._config, self._max_tokens).summary(messages)
        async with self._client() as client:
            resp = await client.post(CHAT_COMPLETIONS_PATH, json=payload)
        if resp.status_code != 200:
            raise RuntimeError(f"LLM API error: {resp.status_code} {resp.text}")
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def _build_payload(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        system: str | None,
        stream: bool,
    ) -> dict:
        return PayloadBuilder(self._config, self._max_tokens).chat(
            messages,
            tools,
            system,
            stream=stream,
        )
