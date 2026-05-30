import json
from uuid import UUID, uuid4

from loguru import logger


class Tracer:
    async def trace_llm_call(
        self,
        chat_id: UUID,
        model: str,
        messages: list[dict],
        response: dict,
        latency_ms: int,
        usage: dict | None = None,
    ) -> UUID | None:
        raise NotImplementedError


class NullTracer(Tracer):
    async def trace_llm_call(self, *args, **kwargs) -> UUID | None:
        return None


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
        usage: dict | None = None,
    ) -> UUID:
        completion = response.get("content", "")
        tool_calls = response.get("tool_calls")
        completion_data = {"content": completion}
        if tool_calls:
            completion_data["tool_calls"] = tool_calls

        prompt_tokens = usage.get("prompt_tokens") if usage else None
        completion_tokens = usage.get("completion_tokens") if usage else None

        trace_id = uuid4()
        logger.debug("PostgresTracer: inserting trace id={id} chat={chat} model={m} latency={l}ms "
                      "prompt_tokens={pt} completion_tokens={ct}",
                      id=trace_id, chat=chat_id, m=model, l=latency_ms,
                      pt=prompt_tokens, ct=completion_tokens)
        try:
            await self._db.execute(
                """INSERT INTO llm_traces (id, chat_id, model, prompt_text, completion_text,
                   prompt_tokens, completion_tokens, latency_ms)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
                trace_id,
                chat_id,
                model,
                json.dumps(messages),
                json.dumps(completion_data) if (completion or tool_calls) else None,
                prompt_tokens if prompt_tokens else _estimate_tokens(messages),
                completion_tokens if completion_tokens else _estimate_tokens([{"content": completion}]),
                latency_ms,
            )
            logger.debug("PostgresTracer: insert succeeded trace_id={id}", id=trace_id)
        except Exception:
            logger.opt(exception=True).error("PostgresTracer: insert failed trace_id={id}", id=trace_id)


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
