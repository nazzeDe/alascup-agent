"""tests for ProbeResolver — kernel-aware bpftrace probe selection."""

import asyncio

import pytest
from pathlib import Path

pytestmark = pytest.mark.unit


class TestProbeResolver:
    """ProbeResolver.resolve() — 内核版本 → 探针变体路径选择。"""

    def test_resolve_exact_match(self, tmp_path: Path) -> None:
        """kernel 6.12 精确匹配 probes/linux-6.12+/execsnoop.bt"""
        from src.tools.perception.ebpf.resolver import ProbeResolver

        (tmp_path / "linux-6.12+").mkdir(parents=True)
        (tmp_path / "generic").mkdir(parents=True)
        (tmp_path / "linux-6.12+" / "execsnoop.bt").write_text("// 6.12+")
        (tmp_path / "generic" / "execsnoop.bt").write_text("// generic")

        resolver = ProbeResolver(
            probes_root=tmp_path,
            version_string="Linux version 6.12.0-generic (buildd@lcy02) (x86_64-linux-gnu)",
        )
        result = resolver.resolve("execsnoop.bt")

        assert result == tmp_path / "linux-6.12+" / "execsnoop.bt"

    def test_resolve_best_match_newer_kernel(self, tmp_path: Path) -> None:
        """kernel 6.15 有 linux-6.6+ 和 linux-6.12+ 两个 dir → 选 6.12+（最高 <= 6.15）"""
        from src.tools.perception.ebpf.resolver import ProbeResolver

        (tmp_path / "linux-6.6+").mkdir(parents=True)
        (tmp_path / "linux-6.12+").mkdir(parents=True)
        (tmp_path / "generic").mkdir(parents=True)
        (tmp_path / "linux-6.6+" / "execsnoop.bt").write_text("// 6.6+")
        (tmp_path / "linux-6.12+" / "execsnoop.bt").write_text("// 6.12+")
        (tmp_path / "generic" / "execsnoop.bt").write_text("// generic")

        resolver = ProbeResolver(
            probes_root=tmp_path,
            version_string="Linux version 6.15.0-rc1 (buildd@lcy02) (x86_64)",
        )
        result = resolver.resolve("execsnoop.bt")

        assert result == tmp_path / "linux-6.12+" / "execsnoop.bt"

    def test_resolve_fallback_to_generic(self, tmp_path: Path) -> None:
        """kernel 6.1 无匹配 dir → 降级 generic"""
        from src.tools.perception.ebpf.resolver import ProbeResolver

        (tmp_path / "linux-6.12+").mkdir(parents=True)
        (tmp_path / "generic").mkdir(parents=True)
        (tmp_path / "linux-6.12+" / "execsnoop.bt").write_text("// 6.12+")
        (tmp_path / "generic" / "execsnoop.bt").write_text("// generic")

        resolver = ProbeResolver(
            probes_root=tmp_path,
            version_string="Linux version 6.1.0-generic (buildd@lcy02)",
        )
        result = resolver.resolve("execsnoop.bt")

        assert result == tmp_path / "generic" / "execsnoop.bt"

    def test_resolve_not_found_returns_none(self, tmp_path: Path) -> None:
        """脚本不存在于任何位置 → 返回 None"""
        from src.tools.perception.ebpf.resolver import ProbeResolver

        (tmp_path / "generic").mkdir(parents=True)

        resolver = ProbeResolver(
            probes_root=tmp_path,
            version_string="Linux version 6.12.0-generic",
        )
        result = resolver.resolve("nonexistent.bt")

        assert result is None

    def test_resolve_cachyos_version(self, tmp_path: Path) -> None:
        """CachyOS 版本 '7.0.12-1-cachyos' → 解析为 (7, 0) → 匹配 6.12+"""
        from src.tools.perception.ebpf.resolver import ProbeResolver

        (tmp_path / "linux-6.12+").mkdir(parents=True)
        (tmp_path / "generic").mkdir(parents=True)
        (tmp_path / "linux-6.12+" / "execsnoop.bt").write_text("// 6.12+")

        resolver = ProbeResolver(
            probes_root=tmp_path,
            version_string="Linux version 7.0.12-1-cachyos (gcc) (x86_64)",
        )
        result = resolver.resolve("execsnoop.bt")

        assert result == tmp_path / "linux-6.12+" / "execsnoop.bt"

    def test_resolve_unreadable_version(self, tmp_path: Path) -> None:
        """无法解析内核版本 → 降级 generic"""
        from src.tools.perception.ebpf.resolver import ProbeResolver

        (tmp_path / "generic").mkdir(parents=True)
        (tmp_path / "generic" / "execsnoop.bt").write_text("// generic")

        resolver = ProbeResolver(probes_root=tmp_path, version_string="")
        result = resolver.resolve("execsnoop.bt")

        assert result == tmp_path / "generic" / "execsnoop.bt"

    def test_resolve_no_fallback(self, tmp_path: Path) -> None:
        """无 versioned dir 且 generic 未找到 → 返回 None（无 flat 降级）"""
        from src.tools.perception.ebpf.resolver import ProbeResolver

        (tmp_path / "generic").mkdir(parents=True)
        (tmp_path / "execsnoop.bt").write_text("// flat (not reachable)")

        resolver = ProbeResolver(
            probes_root=tmp_path,
            version_string="Linux version 6.1.0-generic",
        )
        result = resolver.resolve("execsnoop.bt")

        assert result is None

    def test_resolve_loongarch_6_6_version(self, tmp_path: Path) -> None:
        """loongarch64 kernel 6.6.0-32.17.v2505.kyl1 版本解析为 (6, 6) → 匹配 linux-6.6+/"""
        from src.tools.perception.ebpf.resolver import ProbeResolver

        (tmp_path / "linux-6.6+").mkdir(parents=True)
        (tmp_path / "generic").mkdir(parents=True)
        (tmp_path / "linux-6.6+" / "tcpdrop.bt").write_text("// 6.6+")
        (tmp_path / "generic" / "tcpdrop.bt").write_text("// generic")

        resolver = ProbeResolver(
            probes_root=tmp_path,
            version_string="Linux version 6.6.0-32.17.v2505.kyl1.loongarch64 (KYLINSOFT@1b1c18b8ee97) (gcc (GCC) 12.3.1)",
        )
        result = resolver.resolve("tcpdrop.bt")

        assert result == tmp_path / "linux-6.6+" / "tcpdrop.bt"

    def test_resolve_6_6_best_match_over_generic(self, tmp_path: Path) -> None:
        """kernel 6.6: linux-6.6+/ 和 linux-6.12+/ 都存在 → 选 6.6+（<=6.6 最高版本）"""
        from src.tools.perception.ebpf.resolver import ProbeResolver

        (tmp_path / "linux-6.6+").mkdir(parents=True)
        (tmp_path / "linux-6.12+").mkdir(parents=True)
        (tmp_path / "generic").mkdir(parents=True)
        (tmp_path / "linux-6.6+" / "oomkill.bt").write_text("// 6.6+")
        (tmp_path / "linux-6.12+" / "oomkill.bt").write_text("// 6.12+")
        (tmp_path / "generic" / "oomkill.bt").write_text("// generic")

        resolver = ProbeResolver(
            probes_root=tmp_path,
            version_string="Linux version 6.6.0-32.17.v2505.kyl1.loongarch64",
        )
        result = resolver.resolve("oomkill.bt")

        assert result == tmp_path / "linux-6.6+" / "oomkill.bt"

    def test_resolve_6_6_fallback_to_generic(self, tmp_path: Path) -> None:
        """6.6 探针不在 6.6+/ 但 generic 存在 → 降级 generic"""
        from src.tools.perception.ebpf.resolver import ProbeResolver

        (tmp_path / "linux-6.6+").mkdir(parents=True)
        (tmp_path / "generic").mkdir(parents=True)
        (tmp_path / "linux-6.6+" / "tcpdrop.bt").write_text("// 6.6+")
        (tmp_path / "generic" / "execsnoop.bt").write_text("// generic")

        resolver = ProbeResolver(
            probes_root=tmp_path,
            version_string="Linux version 6.6.0-32.17.v2505.kyl1.loongarch64",
        )
        result = resolver.resolve("execsnoop.bt")

        assert result == tmp_path / "generic" / "execsnoop.bt"


class TestProbeResolverIntegration:
    """集成测试：用真实 probes/ 目录和 6.6 loongarch64 版本字符串端到端解析。"""

    _VERSION_LOONGARCH_6_6 = (
        "Linux version 6.6.0-32.17.v2505.kyl1.loongarch64 "
        "(KYLINSOFT@1b1c18b8ee97) (gcc (GCC) 12.3.1) "
        "(GNU ld (GNU Binutils) 2.41) #1 SMP Fri May 15 16:17:58 UTC 2026"
    )

    @staticmethod
    def _make_resolver():
        from src.tools.perception.ebpf.resolver import ProbeResolver
        from pathlib import Path

        probes_root = (
            Path(__file__).resolve().parent.parent
            / "src"
            / "tools"
            / "perception"
            / "ebpf"
            / "probes"
        )
        return ProbeResolver(
            probes_root=probes_root,
            version_string=TestProbeResolverIntegration._VERSION_LOONGARCH_6_6,
        )

    def test_resolve_oomkill_to_6_6(self) -> None:
        """oomkill.bt → probes/linux-6.6+/oomkill.bt"""
        resolver = self._make_resolver()
        result = resolver.resolve("oomkill.bt")
        assert result is not None
        assert result.parent.name == "linux-6.6+"
        assert result.name == "oomkill.bt"

    def test_resolve_proc_exit_to_6_6(self) -> None:
        """proc_exit.bt → probes/linux-6.6+/proc_exit.bt"""
        resolver = self._make_resolver()
        result = resolver.resolve("proc_exit.bt")
        assert result is not None
        assert result.parent.name == "linux-6.6+"
        assert result.name == "proc_exit.bt"

    def test_resolve_tcpdrop_to_6_6(self) -> None:
        """tcpdrop.bt → probes/linux-6.6+/tcpdrop.bt（不是 generic 的 kprobe:tcp_drop）"""
        resolver = self._make_resolver()
        result = resolver.resolve("tcpdrop.bt")
        assert result is not None
        assert result.parent.name == "linux-6.6+"
        assert result.name == "tcpdrop.bt"

    def test_resolve_biolatency_to_6_6(self) -> None:
        """biolatency.bt → probes/linux-6.6+/biolatency.bt"""
        resolver = self._make_resolver()
        result = resolver.resolve("biolatency.bt")
        assert result is not None
        assert result.parent.name == "linux-6.6+"
        assert result.name == "biolatency.bt"

    def test_resolve_execsnoop_fallback_generic(self) -> None:
        """execsnoop.bt 不在 6.6+/ → 降级 probes/generic/execsnoop.bt"""
        resolver = self._make_resolver()
        result = resolver.resolve("execsnoop.bt")
        assert result is not None
        assert result.parent.name == "generic"
        assert result.name == "execsnoop.bt"

    def test_resolve_syscount_fallback_generic(self) -> None:
        """syscount.bt 不在 6.6+/ → 降级 probes/generic/syscount.bt"""
        resolver = self._make_resolver()
        result = resolver.resolve("syscount.bt")
        assert result is not None
        assert result.parent.name == "generic"
        assert result.name == "syscount.bt"


class TestEbpfRuntime:
    def test_runtime_watch_reports_unregistered_probe(self, tmp_path: Path) -> None:
        from src.tools.perception.ebpf.runtime import EbpfRuntime, WatchKind

        runtime = EbpfRuntime(
            probes_root=tmp_path, version_string="Linux version 6.6.0"
        )

        assert runtime.watch(WatchKind.PROCESS_EXEC) == {
            "events": [],
            "probe_status": "not_registered",
        }

    @pytest.mark.asyncio
    async def test_runtime_capture_hides_script_resolution(
        self, tmp_path: Path
    ) -> None:
        from src.tools.perception.ebpf.runtime import EbpfRuntime, ProbeKind

        class FakeExecutor:
            async def execute(self, script_name: str, timeout: float) -> list[dict]:
                assert script_name == "syscount.bt"
                assert timeout == 15.0
                return [{"ok": True}]

        runtime = EbpfRuntime(
            probes_root=tmp_path,
            version_string="not parseable",
            executor=FakeExecutor(),
        )

        assert await runtime.capture(ProbeKind.SYSCALL_STATS, 3) == {
            "events": [{"ok": True}]
        }

    @pytest.mark.asyncio
    async def test_runtime_serializes_on_demand_probes(self, tmp_path: Path) -> None:
        from src.tools.perception.ebpf.runtime import EbpfRuntime, ProbeKind

        active = 0
        max_active = 0

        class FakeExecutor:
            async def execute(self, script_name: str, timeout: float) -> list[dict]:
                nonlocal active, max_active
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0)
                active -= 1
                return [{"script": script_name}]

        runtime = EbpfRuntime(
            probes_root=tmp_path,
            version_string="not parseable",
            executor=FakeExecutor(),
        )

        results = await asyncio.gather(
            runtime.capture(ProbeKind.SYSCALL_STATS, 1),
            runtime.capture(ProbeKind.TCP_DROPS, 1),
        )

        assert max_active == 1
        assert results == [
            {"events": [{"script": "syscount.bt"}]},
            {"events": [{"script": "tcpdrop.bt"}]},
        ]
