from src.agent.messages import normalize_message


class ContextManager:
    def __init__(
        self,
        window_size: int = 128000,
        threshold: float = 0.7,
        summarizer=None,
        compact_llm=None,
        keep_recent: int = 4,
        max_result_chars: int = 500,
    ) -> None:
        self.window_size = window_size
        self.threshold = threshold
        self._summarizer = summarizer
        self._compact_llm = compact_llm
        self.keep_recent = keep_recent
        self.max_result_chars = max_result_chars

    def count_tokens(self, messages: list) -> int:
        """Approximate token count using chars//4 heuristic (DeepSeek-compatible)."""
        total = 0
        for m in messages:
            content = ""
            if isinstance(m, dict):
                content = str(m.get("content", ""))
            else:
                content = str(getattr(m, "content", ""))
            total += max(1, len(content) // 4) if content else 0
        return max(1, total)

    def needs_compression(self, current_tokens: int) -> bool:
        return current_tokens >= self.window_size * self.threshold

    def _truncate_tool_results(self, messages: list) -> list[dict]:
        """Truncate long tool result content. Normalizes messages to dicts."""
        result: list[dict] = []
        for m in messages:
            d = m if isinstance(m, dict) else normalize_message(m)
            if d.get("role") == "tool":
                content = d.get("content", "")
                if len(content) > self.max_result_chars:
                    d = {
                        **d,
                        "content": content[: self.max_result_chars] + "...[truncated]",
                    }
            result.append(d)
        return result

    async def compress(self, messages: list) -> list:
        # Layer 1: Truncate long tool results
        truncated = self._truncate_tool_results(messages)
        if len(truncated) < 8:
            return truncated

        if not self.needs_compression(self.count_tokens(truncated)):
            return truncated

        # Layer 2: Summarize early messages with lightweight LLM
        summarizer = self._compact_llm or self._summarizer
        if summarizer is None:
            return truncated

        to_summarize = truncated[: -self.keep_recent]
        recent = truncated[-self.keep_recent :]

        if self._compact_llm:
            system_prompt = (
                "Text only, no tools. 总结以下对话。提取：关键请求、技术发现、已完成操作、待处理事项。"
                "用 <summary> 标签包裹。"
            )
            user_prompt = _format_for_summary(to_summarize) + "\n\n---\n\n请生成摘要。"
            summary = await self._compact_llm(system_prompt, user_prompt)
        else:
            summary = await self._summarizer(to_summarize)

        return [
            {"role": "system", "content": f"[Conversation summary] {summary}"}
        ] + recent


def _format_for_summary(messages: list) -> str:
    """Format messages for summarization. Normalizes to dicts if needed."""
    lines = []
    for m in messages:
        d = m if isinstance(m, dict) else normalize_message(m)
        role = d.get("role", "")
        content = d.get("content", "")
        lines.append(f"[{role}]: {content}")
    return "\n".join(lines)
