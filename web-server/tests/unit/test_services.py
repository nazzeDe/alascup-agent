import uuid
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.unit


class TestSessionManager:
    @pytest.mark.asyncio
    async def test_create_session(self):
        from src.services.session_manager import InMemorySessionManager

        mgr = InMemorySessionManager()
        session = await mgr.create_session()
        assert session.id is not None
        assert session.messages == []
        assert session.executed_tool_list == []

    @pytest.mark.asyncio
    async def test_get_session(self):
        from src.services.session_manager import InMemorySessionManager

        mgr = InMemorySessionManager()
        created = await mgr.create_session()
        retrieved = await mgr.get_session(created.id)
        assert retrieved.id == created.id

    @pytest.mark.asyncio
    async def test_get_session_not_found(self):
        from src.services.session_manager import InMemorySessionManager

        mgr = InMemorySessionManager()
        with pytest.raises(KeyError):
            await mgr.get_session(uuid.uuid4())

    @pytest.mark.asyncio
    async def test_list_sessions(self):
        from src.services.session_manager import InMemorySessionManager

        mgr = InMemorySessionManager()
        await mgr.create_session()
        await mgr.create_session()
        sessions = await mgr.list_sessions()
        assert len(sessions) == 2

    @pytest.mark.asyncio
    async def test_add_message(self):
        from src.models.message import Message, MessageType
        from src.services.session_manager import InMemorySessionManager

        mgr = InMemorySessionManager()
        session = await mgr.create_session()
        msg = Message(
            message_id=uuid.uuid4(),
            chat_id=session.id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            type=MessageType.USER,
            content="hello",
        )
        await mgr.add_message(session.id, msg)
        retrieved = await mgr.get_session(session.id)
        assert len(retrieved.messages) == 1


class TestPromptManager:
    def test_load_prompt(self, tmp_path):
        from src.services.prompt_manager import PromptManager

        prompt_path = tmp_path / "prompt.md"
        prompt_path.write_text("## identity\nYou are an ops agent.")
        mgr = PromptManager(prompt_path=prompt_path)

        result = mgr.build_system_prompt()
        assert "You are an ops agent." in result

    def test_default_prompt_when_no_file(self):
        from src.services.prompt_manager import PromptManager

        mgr = PromptManager()
        result = mgr.build_system_prompt()
        assert "AI 运维 Agent" in result


class TestContextManager:
    def test_count_tokens_positive(self):
        from src.services.context_manager import ContextManager

        cm = ContextManager(window_size=10000, threshold=0.7)
        tokens = cm.count_tokens([{"role": "user", "content": "hello world"}])
        assert tokens > 0

    def test_needs_compression_under_threshold(self):
        from src.services.context_manager import ContextManager

        cm = ContextManager(window_size=10000, threshold=0.7)
        assert not cm.needs_compression(1000)

    def test_needs_compression_over_threshold(self):
        from src.services.context_manager import ContextManager

        cm = ContextManager(window_size=10000, threshold=0.7)
        assert cm.needs_compression(8000)

    @pytest.mark.asyncio
    async def test_compress_with_summarizer(self):
        from src.services.context_manager import ContextManager

        async def fake_summarizer(messages):
            return "summarized content"

        cm = ContextManager(
            window_size=10, threshold=0.5, summarizer=fake_summarizer, keep_recent=2
        )
        messages = [{"role": "user", "content": f"msg{i}"} for i in range(10)]
        result = await cm.compress(messages)

        assert len(result) < len(messages)
        assert any("Conversation summary" in m["content"] for m in result)

    @pytest.mark.asyncio
    async def test_compress_no_summarizer_returns_unchanged(self):
        from src.services.context_manager import ContextManager

        cm = ContextManager()
        messages = [{"role": "user", "content": f"msg{i}"} for i in range(10)]
        result = await cm.compress(messages)

        assert len(result) == len(messages)


class TestContextManagerCompression:
    """AG-005: 上下文压缩（两层）。"""

    @pytest.mark.asyncio
    async def test_layer1_truncates_long_tool_results(self):
        """第 1 层：截断超长 tool_result，保留摘要字段。"""
        from src.services.context_manager import ContextManager

        cm = ContextManager(max_result_chars=100)
        messages = [
            {"role": "user", "content": "check CPU"},
            {"role": "tool", "content": "A" * 500},
            {"role": "assistant", "content": "CPU is fine"},
        ]
        result = cm._truncate_tool_results(messages)

        assert len(result[1]["content"]) <= 100 + len("...[truncated]")
        assert "...[truncated]" in result[1]["content"]
        assert result[0]["content"] == "check CPU"
        assert result[2]["content"] == "CPU is fine"

    def test_layer1_preserves_short_tool_results(self):
        """短 tool_result 不截断。"""
        from src.services.context_manager import ContextManager

        cm = ContextManager(max_result_chars=100)
        messages = [{"role": "tool", "content": "short result"}]
        result = cm._truncate_tool_results(messages)
        assert result[0]["content"] == "short result"

    def test_layer1_skips_non_tool_messages(self):
        """非 tool 消息原样保留。"""
        from src.services.context_manager import ContextManager

        cm = ContextManager(max_result_chars=10)
        messages = [
            {"role": "user", "content": "hello world"},
            {"role": "assistant", "content": "I'm fine"},
        ]
        result = cm._truncate_tool_results(messages)
        assert result == messages

    @pytest.mark.asyncio
    async def test_compress_short_list_no_layer2(self):
        """消息不足 8 条时不触发第 2 层。"""
        from src.services.context_manager import ContextManager

        cm = ContextManager(max_result_chars=10)
        messages = [{"role": "user", "content": f"msg{i}"} for i in range(5)]
        result = await cm.compress(messages)
        # Layer 1 still applies if tool_results are present
        assert len(result) == len(messages)

    @pytest.mark.asyncio
    async def test_layer1_sufficient_no_layer2(self):
        """仅截断就降到阈值以下，不调 compact_llm。"""
        from src.services.context_manager import ContextManager

        compact_called = False

        async def mock_compact_llm(system_prompt, user_prompt):
            nonlocal compact_called
            compact_called = True
            return "should not be called"

        cm = ContextManager(
            window_size=500,
            threshold=0.7,
            max_result_chars=10,
            compact_llm=mock_compact_llm,
        )
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "tool", "content": "A" * 200},
            {"role": "assistant", "content": "ok"},
        ] * 3

        await cm.compress(messages)
        assert not compact_called

    @pytest.mark.asyncio
    async def test_layer2_called_when_layer1_insufficient(self):
        """截断后仍超阈值 → 调 compact_llm 生成摘要。"""
        from src.services.context_manager import ContextManager

        async def mock_compact_llm(system_prompt, user_prompt):
            assert "Text only, no tools" in system_prompt
            assert "请生成摘要" in user_prompt
            return "summarized conversation"

        cm = ContextManager(
            window_size=500,
            threshold=0.7,
            max_result_chars=50,
            compact_llm=mock_compact_llm,
            keep_recent=2,
        )
        messages = []
        for i in range(15):
            messages.append({"role": "user", "content": f"message {i} " + "x" * 100})

        result = await cm.compress(messages)

        assert len(result) < len(messages)
        assert result[0]["role"] == "system"
        assert "summarized conversation" in result[0]["content"]

    @pytest.mark.asyncio
    async def test_layer2_preserves_recent_messages(self):
        """第 2 层：近期消息和工具结果优先保留原始内容。"""
        from src.services.context_manager import ContextManager

        async def mock_compact_llm(system_prompt, user_prompt):
            return "summary"

        cm = ContextManager(
            window_size=500,
            threshold=0.7,
            max_result_chars=30,
            compact_llm=mock_compact_llm,
            keep_recent=3,
        )
        messages = []
        for i in range(12):
            messages.append({"role": "user", "content": f"msg {i} " + "y" * 80})
        messages[-3] = {"role": "tool", "content": "recent tool result"}
        messages[-2] = {"role": "assistant", "content": "recent assistant"}
        messages[-1] = {"role": "user", "content": "recent user"}

        result = await cm.compress(messages)

        assert result[-3]["content"] == "recent tool result"
        assert result[-2]["content"] == "recent assistant"
        assert result[-1]["content"] == "recent user"

    @pytest.mark.asyncio
    async def test_no_compact_llm_layer1_only(self):
        """无 compact_llm 时仅截断，不崩溃。"""
        from src.services.context_manager import ContextManager

        cm = ContextManager(window_size=500, threshold=0.7, max_result_chars=20)
        messages = [
            {"role": "user", "content": "x" * 50},
            {"role": "tool", "content": "Y" * 500},
        ] * 6

        result = await cm.compress(messages)
        for m in result:
            if m["role"] == "tool":
                assert len(m["content"]) <= 20 + len("...[truncated]")

    @pytest.mark.asyncio
    async def test_compressed_tokens_below_threshold(self):
        """AG-005 核心：压缩后 token 数降至安全阈值以下。"""
        from src.services.context_manager import ContextManager

        async def mock_compact_llm(system_prompt, user_prompt):
            return "brief summary"

        cm = ContextManager(
            window_size=1000,
            threshold=0.7,
            max_result_chars=50,
            compact_llm=mock_compact_llm,
            keep_recent=2,
        )
        # 构造大量长消息，token 远超阈值
        messages = []
        for i in range(20):
            messages.append({"role": "user", "content": "x" * 200})

        result = await cm.compress(messages)
        tokens_after = cm.count_tokens(result)
        threshold = cm.window_size * cm.threshold

        assert tokens_after < threshold
