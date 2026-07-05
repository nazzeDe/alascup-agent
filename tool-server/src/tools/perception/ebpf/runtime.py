from __future__ import annotations

from pathlib import Path

from .resolver import ProbeResolver
from .runner import run_on_demand
from .subscription import SubscriptionManager


class EbpfRuntime:
    """Own probe resolution, subscriptions, on-demand probes, and shutdown."""

    def __init__(
        self,
        probes_root: Path | None = None,
        version_string: str | None = None,
    ) -> None:
        root = probes_root or Path(__file__).parent / "probes"
        self._resolver = ProbeResolver(root, version_string=version_string)
        self._subscriptions = SubscriptionManager(resolve_fn=self.resolve)

    def resolve(self, script_name: str) -> Path | None:
        return self._resolver.resolve(script_name)

    async def start_all(self, enabled: list[str] | None = None) -> None:
        await self._subscriptions.start_all(enabled=enabled)

    async def shutdown(self) -> None:
        await self._subscriptions.shutdown()

    def watch(self, script_name: str) -> dict:
        return {
            "events": self._subscriptions.drain(script_name),
            "probe_status": self._subscriptions.probe_status(script_name),
        }

    async def trace(self, script_name: str, duration: int) -> dict:
        return {
            "events": await run_on_demand(
                script_name, timeout=float(duration), resolve_fn=self.resolve
            )
        }
