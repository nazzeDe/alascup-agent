"""
resolver.py — bpftrace 探针跨内核选择器。

根据宿主内核版本自动选择最合适的探针变体，失败时逐级降级。
"""
from __future__ import annotations

import re
from pathlib import Path

from loguru import logger

# sets/ 目录命名模式: linux-<major>.<minor>+
_SET_PATTERN = re.compile(r"^linux-(\d+)\.(\d+)\+$")
# /proc/version 版本提取
_VERSION_PATTERN = re.compile(r"Linux version (\d+)\.(\d+)")


class ProbeResolver:
    """根据内核版本选择最合适的探针脚本变体。

    降级链: probes/<best-match>/ → probes/generic/ → None
    """

    def __init__(
        self,
        probes_root: Path,
        version_string: str | None = None,
    ) -> None:
        self._probes_root = probes_root
        self._version_raw = version_string
        self._version: tuple[int, int] | None = None  # cached

    # ── 公共接口 ──────────────────────────────────────────────────

    def resolve(self, script_name: str) -> Path | None:
        """为给定脚本选择最佳变体，逐级降级。

        1. 解析内核版本 → 匹配最佳 probes/linux-X.Y+/ 目录
        2. 该目录无同名脚本 → 降级 probes/generic/
        3. generic 也无 → 返回 None
        """
        version = self._get_version()
        set_name = self._find_best_set(version) if version else None

        # Tier 1: best-match set
        if set_name is not None:
            candidate = self._probes_root / set_name / script_name
            if candidate.exists():
                logger.info(
                    "resolved probe={} set={} kernel={}.{}",
                    script_name, set_name, version[0], version[1],
                )
                return candidate

        # Tier 2: generic fallback
        generic = self._probes_root / "generic" / script_name
        if generic.exists():
            reason = f"kernel {version[0]}.{version[1]}" if version else "unknown kernel"
            logger.info(
                "resolved probe={} set=generic ({} - no matching versioned set)",
                script_name, reason,
            )
            return generic

        # Tier 3: nothing
        logger.error("probe={} not found in any versioned directory", script_name)
        return None

    # ── 内部 ──────────────────────────────────────────────────────

    def _get_version(self) -> tuple[int, int] | None:
        """获取内核主次版本号（缓存）。"""
        if self._version is not None:
            return self._version
        self._version = self._parse_version()
        return self._version

    def _parse_version(self) -> tuple[int, int] | None:
        """解析内核版本字符串或 /proc/version。

        注入 version_string → 直接解析。否则读 /proc/version。
        """
        raw = self._version_raw
        if raw is None:
            try:
                raw = Path("/proc/version").read_text()
            except (FileNotFoundError, PermissionError):
                logger.warning("cannot read /proc/version, probe resolution limited to generic")
                return None

        m = _VERSION_PATTERN.search(raw)
        if m is None:
            logger.warning("unparseable kernel version from: {!r}", raw[:80])
            return None
        return int(m.group(1)), int(m.group(2))

    def _find_best_set(self, version: tuple[int, int]) -> str | None:
        """扫描 probes/linux-X.Y+/ 目录，返回最高 X.Y <= 当前内核的目录名。"""
        sets_dir = self._probes_root
        if not sets_dir.is_dir():
            return None

        candidates: list[tuple[int, int, str]] = []
        for entry in sets_dir.iterdir():
            if not entry.is_dir():
                continue
            m = _SET_PATTERN.match(entry.name)
            if m is None:
                continue
            set_ver = (int(m.group(1)), int(m.group(2)))
            if set_ver <= version:
                candidates.append((*set_ver, entry.name))

        if not candidates:
            return None

        # 选最高版本
        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return candidates[0][2]


# ── 模块级单例 ────────────────────────────────────────────────────────

_resolver: ProbeResolver | None = None


def resolve(script_name: str) -> Path | None:
    """解析探针脚本到最佳变体路径（模块级便捷函数）。

    首次调用时创建全局 ProbeResolver 实例，后续调用复用。
    """
    global _resolver
    if _resolver is None:
        _probes_root = Path(__file__).parent / "probes"
        _resolver = ProbeResolver(_probes_root)
    return _resolver.resolve(script_name)
