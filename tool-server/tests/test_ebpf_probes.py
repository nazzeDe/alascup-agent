"""tests for 6.6+ loongarch64 probe variant content and structure."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_PROBES_ROOT = Path(__file__).resolve().parent.parent / "src" / "tools" / "perception" / "ebpf" / "probes"
_SET_6_6 = _PROBES_ROOT / "linux-6.6+"


class TestProbeVariants6_6:
    """probes/linux-6.6+/ 目录和探针变体文件存在性测试。"""

    def test_directory_exists(self) -> None:
        """6.6+ 目录存在"""
        assert _SET_6_6.is_dir(), f"missing: {_SET_6_6}"

    def test_oomkill_exists_and_valid(self) -> None:
        """oomkill.bt 存在且使用 6.6 特定 tracepoint 字段"""
        f = _SET_6_6 / "oomkill.bt"
        assert f.is_file(), f"missing: {f}"
        content = f.read_text()
        assert "tracepoint:oom:mark_victim" in content
        # 6.6 特定字段：直接 pid (非 args->pid)
        assert "total_vm" in content
        assert "anon_rss" in content
        assert "oom_score_adj" in content

    def test_proc_exit_exists_and_valid(self) -> None:
        """proc_exit.bt 存在且使用 6.6 sched_process_exit 字段（无 exit code，有 prio，comm 直接 %s）"""
        f = _SET_6_6 / "proc_exit.bt"
        assert f.is_file(), f"missing: {f}"
        content = f.read_text()
        assert "tracepoint:sched:sched_process_exit" in content
        assert "prio" in content
        # 6.6 没有 exit_code 和 group_dead
        assert "exit_code" not in content
        # bpftrace 6.6 char[16] 已是 string，printf 直接用 args->comm 配合 %s
        assert 'args->comm' in content
        assert 'str(args->comm)' not in content

    def test_tcpdrop_exists_and_valid(self) -> None:
        """tcpdrop.bt 存在且使用 tcp_retransmit_skb tracepoint（替代 tcp_drop）"""
        f = _SET_6_6 / "tcpdrop.bt"
        assert f.is_file(), f"missing: {f}"
        content = f.read_text()
        assert "tracepoint:tcp:tcp_retransmit_skb" in content
        # 不应引用不存在的 kprobe:tcp_drop
        assert "kprobe:tcp_drop" not in content

    def test_biolatency_exists_and_valid(self) -> None:
        """biolatency.bt 存在且使用 blk_mq kprobes（替代 blk_account_io_*）"""
        f = _SET_6_6 / "biolatency.bt"
        assert f.is_file(), f"missing: {f}"
        content = f.read_text()
        # 用 blk_mq kprobes 替代不存在的 blk_account_io_*
        assert "kprobe:blk_mq_start_request" in content
        assert "kprobe:blk_mq_complete_request" in content
        # 不引用不存在的 kprobe
        assert "kprobe:blk_account_io_start" not in content
        assert "kprobe:blk_account_io_done" not in content

    def test_all_probes_have_json_output(self) -> None:
        """6.6+ 目录下所有 .bt 文件输出 JSON 格式"""
        for f in sorted(_SET_6_6.glob("*.bt")):
            content = f.read_text()
            assert "printf" in content, f"{f.name}: missing printf for JSON output"
