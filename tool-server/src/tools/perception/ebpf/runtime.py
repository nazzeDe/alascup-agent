from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .resolver import ProbeResolver
from .runner import BpftraceProbeExecutor, ProbeExecutor, on_demand_timeout
from .subscription import SubscriptionManager

DEFAULT_MAX_CONCURRENT_PROBES = 1


class ProbeKind(StrEnum):
    SYSCALL_STATS = "syscall_stats"
    SLOW_SYSCALLS = "slow_syscalls"
    TCP_DROPS = "tcp_drops"
    IO_LATENCY = "io_latency"
    OOM_EVENTS = "oom_events"


class WatchKind(StrEnum):
    PROCESS_EXEC = "process_exec"
    PROCESS_EXIT = "process_exit"
    TCP_CONNECTIONS = "tcp_connections"


@dataclass(frozen=True)
class _ProbeSpec:
    script_name: str
    default_duration: int


_PROBE_SPECS: dict[ProbeKind, _ProbeSpec] = {
    ProbeKind.SYSCALL_STATS: _ProbeSpec("syscount.bt", 10),
    ProbeKind.SLOW_SYSCALLS: _ProbeSpec("syscall_slow.bt", 5),
    ProbeKind.TCP_DROPS: _ProbeSpec("tcpdrop.bt", 10),
    ProbeKind.IO_LATENCY: _ProbeSpec("biolatency.bt", 10),
    ProbeKind.OOM_EVENTS: _ProbeSpec("oomkill.bt", 30),
}

_WATCH_SCRIPTS: dict[WatchKind, str] = {
    WatchKind.PROCESS_EXEC: "execsnoop.bt",
    WatchKind.PROCESS_EXIT: "proc_exit.bt",
    WatchKind.TCP_CONNECTIONS: "tcpconn.bt",
}


class EbpfRuntime:
    """Own probe resolution, subscriptions, on-demand probes, and shutdown."""

    def __init__(
        self,
        probes_root: Path | None = None,
        version_string: str | None = None,
        max_concurrent_probes: int = DEFAULT_MAX_CONCURRENT_PROBES,
        executor: ProbeExecutor | None = None,
    ) -> None:
        if max_concurrent_probes < 1:
            raise ValueError("max_concurrent_probes must be at least 1")
        root = probes_root or Path(__file__).parent / "probes"
        self._resolver = ProbeResolver(root, version_string=version_string)
        self._subscriptions = SubscriptionManager(resolve_fn=self._resolve)
        self._on_demand_limit = asyncio.Semaphore(max_concurrent_probes)
        self._executor = executor or BpftraceProbeExecutor(resolve_fn=self._resolve)

    def _resolve(self, script_name: str) -> Path | None:
        return self._resolver.resolve(script_name)

    async def start_all(self) -> None:
        await self._subscriptions.start_all()

    async def shutdown(self) -> None:
        await self._subscriptions.shutdown()

    def watch(self, probe: WatchKind) -> dict:
        try:
            kind = WatchKind(probe)
        except ValueError:
            return {
                "events": [],
                "probe_status": "unsupported",
                "error": "unknown_watch_probe",
            }
        script_name = _WATCH_SCRIPTS[kind]
        return {
            "events": self._subscriptions.drain(script_name),
            "probe_status": self._subscriptions.probe_status(script_name),
        }

    async def capture(self, probe: ProbeKind, duration: int | None = None) -> dict:
        try:
            kind = ProbeKind(probe)
        except ValueError:
            return _capture_error("unknown_probe", str(probe))

        spec = _PROBE_SPECS[kind]
        effective_duration = spec.default_duration if duration is None else duration
        if effective_duration < 1:
            return _capture_error("invalid_duration", kind.value)

        # Keep on-demand probes away from each other and from an OOM-sized burst.
        async with self._on_demand_limit:
            events = await self._executor.execute(
                spec.script_name,
                timeout=on_demand_timeout(spec.script_name, float(effective_duration)),
            )
        return {"events": events}


def _capture_error(code: str, probe: str) -> dict:
    return {"events": [{"error": code, "probe": probe}]}
