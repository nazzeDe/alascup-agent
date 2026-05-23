import json
from uuid import UUID, uuid4


class Tracer:
    async def trace_llm_call(
        self,
        chat_id: UUID,
        model: str,
        messages: list[dict],
        response: dict,
        latency_ms: int,
    ) -> None:
        raise NotImplementedError


class NullTracer(Tracer):
    async def trace_llm_call(self, *args, **kwargs) -> None:
        pass


class PostgresTracer(Tracer):
    def __init__(self, db) -> None:
        self._db = db

    async def trace_llm_call(
        self,
        chat_id: UUID,
        model: str,
        messages: list[dict],
        response: dict,
        latency_ms: int,
    ) -> None:
        completion = response.get("content", "")
        tool_calls = response.get("tool_calls")
        completion_data = {"content": completion}
        if tool_calls:
            completion_data["tool_calls"] = tool_calls

        await self._db.execute(
            """INSERT INTO llm_traces (id, chat_id, model, prompt_text, completion_text,
               prompt_tokens, completion_tokens, latency_ms)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
            uuid4(),
            chat_id,
            model,
            json.dumps(messages),
            json.dumps(completion_data) if (completion or tool_calls) else None,
            _estimate_tokens(messages),
            _estimate_tokens([{"content": completion}]),
            latency_ms,
        )


import tiktoken

_encoder = tiktoken.get_encoding("o200k_base")


def _estimate_tokens(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        try:
            total += len(_encoder.encode(str(m.get("content", ""))))
        except Exception:
            total += max(1, len(str(m.get("content", ""))) // 4)
    return max(1, total)
