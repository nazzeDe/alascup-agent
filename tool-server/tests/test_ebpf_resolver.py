"""tests for ProbeResolver — kernel-aware bpftrace probe selection."""

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
