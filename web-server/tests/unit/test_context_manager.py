"""Token counting and compression tests."""

import pytest

pytestmark = pytest.mark.unit


class TestCountTokens:
    def test_approximate_tokens_chars_div_4(self):
        from src.services.context_manager import ContextManager

        cm = ContextManager()
        msgs = [
            {"role": "user", "content": "The quick brown fox jumps over the lazy dog."}
        ]
        chars = len(msgs[0]["content"])
        tokens = cm.count_tokens(msgs)
        # Uses chars//4 heuristic (DeepSeek-compatible approximation)
        assert tokens == max(1, chars // 4)

    def test_chinese_text_approximation(self):
        from src.services.context_manager import ContextManager

        cm = ContextManager()
        msgs = [{"role": "user", "content": "你好，检查系统状态并诊断性能问题"}]
        chars = len(msgs[0]["content"])
        tokens = cm.count_tokens(msgs)
        # Approximation: chars//4, floors to at least 1
        assert tokens == max(1, chars // 4)

    def test_handles_message_objects(self):
        from src.services.context_manager import ContextManager

        cm = ContextManager()

        class FakeMsg:
            type = "user"
            content = "hello world"

        tokens = cm.count_tokens([FakeMsg()])
        assert tokens > 0

    def test_needs_compression_threshold(self):
        from src.services.context_manager import ContextManager

        cm = ContextManager(window_size=1000, threshold=0.7)
        assert not cm.needs_compression(10)
        assert cm.needs_compression(700)


class TestContextManagerCompression:
    @pytest.mark.asyncio
    async def test_compress_truncates_tool_results_in_short_history(self):
        from src.services.context_manager import ContextManager

        cm = ContextManager(max_result_chars=20)
        messages = [
            {"role": "user", "content": "check"},
            {"role": "assistant", "content": "running"},
            {"role": "tool", "content": "x" * 1_000},
        ]

        result = await cm.compress(messages)

        assert len(result) == len(messages)
        assert len(result[-1]["content"]) <= 20 + len("...[truncated]")
        assert result[-1]["content"].endswith("...[truncated]")
