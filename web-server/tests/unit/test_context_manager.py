"""Token counting and compression tests."""

import pytest

pytestmark = pytest.mark.unit


class TestCountTokens:
    def test_english_tokens_gives_different_result_than_naive(self):
        from src.services.context_manager import ContextManager
        cm = ContextManager()
        msgs = [{"role": "user", "content": "The quick brown fox jumps over the lazy dog."}]
        chars = len(msgs[0]["content"])
        tokens = cm.count_tokens(msgs)
        naive = max(1, chars // 4)
        # tiktoken and chars//4 should differ — that's why we use tiktoken
        assert tokens != naive
        assert tokens < chars  # English: fewer tokens than characters

    def test_chinese_tokens_more_than_chars_div_4(self):
        from src.services.context_manager import ContextManager
        cm = ContextManager()
        # Chinese: 1 char ≈ 1-2 tokens. chars//4 grossly underestimates
        msgs = [{"role": "user", "content": "你好，检查系统状态并诊断性能问题"}]
        chars = len(msgs[0]["content"])
        tokens = cm.count_tokens(msgs)
        naive = max(1, chars // 4)
        assert tokens > naive * 2  # at least 2x the naive estimate

    def test_handles_langgraph_message_objects(self):
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
