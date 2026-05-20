"""OB-001, OB-002: 调试日志门控 + 剖析器门控。"""

import pytest

pytestmark = pytest.mark.unit


class TestDebugLogger:
    """OB-001: 调试日志门控。"""

    def test_ring_buffer_always_works(self):
        """未开启时仅写入内存环形缓冲。"""
        from src.observability.debug_log import DebugLogger

        logger = DebugLogger()
        logger.info("test message")
        recent = logger.recent(10)
        assert any("test message" in r for r in recent)

    def test_disabled_by_default(self):
        """默认不启用文件输出。"""
        from src.observability.debug_log import DebugLogger

        logger = DebugLogger()
        assert not logger.is_enabled()

    def test_debug_level_filtered_when_default_info(self):
        """默认 INFO 级别时 DEBUG 消息被过滤。"""
        from src.observability.debug_log import DebugLogger

        logger = DebugLogger()
        logger.debug("debug msg")
        logger.info("info msg")

        recent = logger.recent(10)
        assert not any("debug msg" in r for r in recent)
        assert any("info msg" in r for r in recent)

    def test_all_levels_when_debug_level(self, monkeypatch):
        """DEBUG 级别时所有消息都记录。"""
        monkeypatch.setenv("ALASCUP_DEBUG_LEVEL", "DEBUG")
        from src.observability.debug_log import DebugLogger

        logger = DebugLogger()
        logger.debug("debug msg")
        logger.info("info msg")

        recent = logger.recent(10)
        assert any("debug msg" in r for r in recent)
        assert any("info msg" in r for r in recent)

    def test_level_order_warn_includes_error(self):
        """WARN 级别时 WARN 和 ERROR 都记录。"""
        from src.observability.debug_log import DebugLogger

        logger = DebugLogger()
        logger._level = "WARN"
        logger.info("info msg")
        logger.warn("warn msg")
        logger.error("error msg")

        recent = logger.recent(10)
        assert not any("info msg" in r for r in recent)
        assert any("warn msg" in r for r in recent)
        assert any("error msg" in r for r in recent)

    def test_filter_keyword_matching(self):
        """ALASCUP_DEBUG_FILTER 只输出含关键词的行。"""
        from src.observability.debug_log import DebugLogger

        logger = DebugLogger()
        logger._filter = "agent"
        logger.info("agent decision")
        logger.info("security check")

        recent = logger.recent(10)
        assert any("agent decision" in r for r in recent)
        assert not any("security check" in r for r in recent)

    def test_ring_buffer_max_500(self):
        """环形缓冲上限 500 条。"""
        from src.observability.debug_log import DebugLogger

        logger = DebugLogger()
        for i in range(600):
            logger.info(f"msg {i}")

        recent = logger.recent(600)
        assert len(recent) == 500
        assert "msg 0" not in "\n".join(recent)
        assert "msg 599" in recent[-1]

    def test_file_output_when_enabled(self, monkeypatch, tmp_path):
        """ALASCUP_DEBUG=1 时写入文件。"""
        monkeypatch.setenv("ALASCUP_DEBUG", "1")
        log_dir = tmp_path / "logs" / "debug"
        log_dir.mkdir(parents=True)

        from src.observability.debug_log import DebugLogger

        logger = DebugLogger()
        # Redirect file output to tmp_path
        logger._file_path = log_dir / "test.log"
        logger.info("file output test")

        assert logger.is_enabled()
        content = logger._file_path.read_text()
        assert "file output test" in content

    def test_file_not_written_when_disabled(self):
        """未开启时不创建文件。"""
        from src.observability.debug_log import DebugLogger

        logger = DebugLogger()
        logger.info("no file")
        assert logger._file_path is None

    def test_recent_returns_last_n(self):
        """recent() 返回最近 N 条。"""
        from src.observability.debug_log import DebugLogger

        logger = DebugLogger()
        for i in range(5):
            logger.info(f"msg {i}")

        recent = logger.recent(3)
        assert len(recent) == 3
        assert "msg 2" in recent[0]
        assert "msg 4" in recent[-1]


class TestProfiler:
    """OB-002: 剖析器门控。"""

    def test_disabled_by_default(self):
        """ALASCUP_PROFILE 未设置时 checkpoint() 为零开销。"""
        from src.observability.profiler import Profiler

        profiler = Profiler()
        assert not profiler.is_enabled()
        # checkpoint should be a no-op
        profiler.checkpoint("test")
        report = profiler.report()
        assert report == {}

    def test_enabled_records_checkpoints(self, monkeypatch):
        """ALASCUP_PROFILE=1 时记录耗时。"""
        monkeypatch.setenv("ALASCUP_PROFILE", "1")
        from src.observability.profiler import Profiler

        profiler = Profiler()
        assert profiler.is_enabled()

        profiler.checkpoint("think")
        profiler.checkpoint("act")
        profiler.checkpoint("observe")

        report = profiler.report()
        assert "think" in report
        assert "act" in report
        assert "observe" in report

    def test_report_marks_slow_operations(self, monkeypatch):
        """报告自动标记 >100ms 的慢操作。"""
        monkeypatch.setenv("ALASCUP_PROFILE", "1")
        from src.observability.profiler import Profiler

        profiler = Profiler()
        profiler.checkpoint("start")

        import time
        time.sleep(0.15)

        profiler.checkpoint("slow_op")
        report = profiler.report()

        assert "slow_op" in report
        assert report["slow_op"]["slow"] is True

    def test_report_includes_total_duration(self, monkeypatch):
        """报告含总耗时。"""
        monkeypatch.setenv("ALASCUP_PROFILE", "1")
        from src.observability.profiler import Profiler

        profiler = Profiler()
        profiler.checkpoint("step1")
        import time as _time
        _time.sleep(0.005)
        profiler.checkpoint("step2")

        report = profiler.report()
        assert report["step2"]["total_ms"] > 0

    def test_reset_clears_checkpoints(self, monkeypatch):
        """reset() 清空检查点，下一轮重新开始。"""
        monkeypatch.setenv("ALASCUP_PROFILE", "1")
        from src.observability.profiler import Profiler

        profiler = Profiler()
        profiler.checkpoint("step1")
        profiler.reset()

        report = profiler.report()
        assert report == {}
