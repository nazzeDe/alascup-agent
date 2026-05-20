import json

from httpx import AsyncClient

from src.config.models import LLMConfig


def classify_error(
    status_code: int, response_text: str, stop_reason: str | None = None
) -> str | None:
    """Classify LLM API errors into standardized error types."""
    if status_code == 200:
        return "max_output_tokens" if stop_reason == "max_tokens" else None

    if status_code == 0:
        return "timeout"

    if status_code in (413, 400) and any(
        kw in response_text.lower()
        for kw in ("too large", "too long", "context", "token")
    ):
        return "prompt_too_long"

    match status_code:
        case 429:
            return "rate_limit"
        case 401 | 403:
            return "auth_failed"
        case 404:
            return "model_unavailable"
        case 500 | 502 | 503 | 529:
            return "server_error"
        case _ if status_code >= 500:
            return "server_error"
        case _ if status_code >= 400:
            return "unknown"

    return None


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

    async def generate(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
    ) -> dict:
        import time
        import uuid

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
                chat_id=uuid.uuid4(),
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
    ):
        payload = self._build_payload(messages, tools, system, stream=True)
        accumulated: dict[int, dict] = {}

        async with self._client() as client:
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
                        yield {"event": "done", "data": "{}"}
                        continue

                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    tool_calls = delta.get("tool_calls")

                    if tool_calls:
                        for tc in tool_calls:
                            idx = tc.get("index", 0)
                            if idx not in accumulated:
                                accumulated[idx] = {
                                    "function": {"name": "", "arguments": ""}
                                }
                            fn = tc.get("function", {})
                            if "name" in fn:
                                accumulated[idx]["function"]["name"] += fn["name"]
                            if "arguments" in fn:
                                accumulated[idx]["function"]["arguments"] += fn[
                                    "arguments"
                                ]

                    if content:
                        yield {
                            "event": "assistant",
                            "data": json.dumps({"delta": content}),
                        }

                for tc in accumulated.values():
                    yield {"event": "tool_call", "data": json.dumps(tc)}

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
