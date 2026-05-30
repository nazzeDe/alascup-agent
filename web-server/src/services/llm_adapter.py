import json
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


def classify_error(
    status_code: int, response_text: str, stop_reason: str | None = None
) -> str | None:
    """Classify LLM API errors into standardized error types."""
    if status_code == 200:
        return "max_output_tokens" if stop_reason == "max_tokens" else None

    if status_code == 0:
        return "timeout"

    if status_code in (413, 400):
        lower = response_text.lower()
        if "max_tokens" in lower or "invalid" in lower:
            return "unknown"
        if any(kw in lower for kw in ("too large", "too long", "context", "token")):
            return "prompt_too_long"

    if mapped := _STATUS_ERROR_MAP.get(status_code):
        return mapped

    if status_code >= 500:
        return "server_error"
    if status_code >= 400:
        return "unknown"

    return None


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


class LLMAdapter:
    def __init__(self, config: LLMConfig, transport=None, tracer=None) -> None:
        self._config = config
        self._transport = transport
        self._tracer = tracer
        self._original_model = config.model
        self._max_tokens = config.max_tokens

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
            headers={
                "Authorization": f"Bearer {self._config.api_key}",
                "Content-Type": "application/json",
            },
            timeout=120,
            transport=self._transport,
        )

    def _streaming_client(self) -> AsyncClient:
        """Client with tighter read timeout for streaming (30s between chunks)."""
        return AsyncClient(
            base_url=self._config.api_url,
            headers={
                "Authorization": f"Bearer {self._config.api_key}",
                "Content-Type": "application/json",
            },
            timeout=Timeout(connect=30, read=30, write=30, pool=30),
            transport=self._transport,
        )

    async def generate(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
        chat_id: UUID | None = None,
    ) -> dict:
        import time
        import uuid as _uuid

        start = time.monotonic()
        payload = self._build_payload(messages, tools, system, stream=False)
        async with self._client() as client:
            resp = await client.post("/chat/completions", json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"LLM API error: {resp.status_code} {resp.text}")
            data = resp.json()
        latency_ms = int((time.monotonic() - start) * 1000)
        choice = data["choices"][0]["message"]
        result = {
            "content": choice.get("content", ""),
            "tool_calls": choice.get("tool_calls"),
        }
        if self._tracer:
            await self._tracer.trace_llm_call(
                chat_id=chat_id if chat_id else _uuid.uuid4(),
                model=self._config.model,
                messages=messages,
                response=result,
                latency_ms=latency_ms,
            )
        return result

    async def generate_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
        chat_id: UUID | None = None,
    ):
        import time as _time

        start = _time.monotonic()
        payload = self._build_payload(messages, tools, system, stream=True)
        accumulated: dict[int, dict] = {}
        accumulated_content = ""

        async with self._streaming_client() as client:
            async with client.stream("POST", "/chat/completions", json=payload) as resp:
                if resp.status_code != 200:
                    text = await resp.aread()
                    yield {
                        "event": "error",
                        "data": json.dumps(
                            {"code": resp.status_code, "message": text.decode()}
                        ),
                    }
                    return

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        await self._trace_stream(chat_id, messages, accumulated_content, accumulated, start)
                        for tc in accumulated.values():
                            yield {"event": "tool_call", "data": json.dumps(tc)}
                        yield {"event": "done", "data": "{}"}
                        break

                    for event in self._process_chunk(data_str, accumulated):
                        yield event
                        if event.get("event") == "assistant":
                            accumulated_content += _extract_delta(event)

    async def _trace_stream(self, chat_id, messages, content, accumulated, start):
        import time as _time
        import uuid as _uuid

        if not self._tracer:
            return
        latency_ms = int((_time.monotonic() - start) * 1000)
        full_response: dict = {"content": content}
        if accumulated:
            full_response["tool_calls"] = list(accumulated.values())
        await self._tracer.trace_llm_call(
            chat_id=chat_id if chat_id else _uuid.uuid4(),
            model=self._config.model,
            messages=messages,
            response=full_response,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _process_chunk(data_str: str, accumulated: dict[int, dict]):
        """Parse a single SSE data line and yield assistant/tool_call events."""
        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            return

        delta = chunk.get("choices", [{}])[0].get("delta", {})
        content = delta.get("content", "")
        reasoning = delta.get("reasoning_content", "")
        tool_calls = delta.get("tool_calls")

        if tool_calls:
            for tc in tool_calls:
                _accumulate_tool_call(tc, accumulated)

        if content or reasoning:
            data: dict[str, str] = {}
            if content:
                data["delta"] = content
            if reasoning:
                data["reasoning_content"] = reasoning
            yield {
                "event": "assistant",
                "data": json.dumps(data),
            }

    async def summarize(self, messages: list[dict]) -> str:
        model = self._config.summary_model or self._config.model
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "Summarize the following conversation concisely, preserving key facts and decisions.",
                },
                {
                    "role": "user",
                    "content": "\n".join(m.get("content", "") for m in messages),
                },
            ],
            "stream": False,
            "max_tokens": 1024,
        }
        async with self._client() as client:
            resp = await client.post("/chat/completions", json=payload)
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
        msgs: list[dict] = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)
        payload: dict = {
            "model": self._config.model,
            "messages": msgs,
            "stream": stream,
            "max_tokens": self._max_tokens,
        }
        if tools:
            payload["tools"] = tools
        return payload
