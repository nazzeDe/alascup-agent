"""Approval bridge: request_id → chat_id + async wait.

Two roles:
  - SSE coroutine (orchestrator): create() then gather_decisions()
  - HTTP endpoint (approval.py): complete() to push a decision
"""

import asyncio


class ApprovalBridge:
    def __init__(self):
        self._chat_ids: dict[str, str] = {}
        self._events: dict[str, asyncio.Event] = {}
        self._pending: dict[str, list[dict]] = {}

    def create(self, request_id: str, chat_id: str) -> None:
        self._chat_ids[request_id] = chat_id
        self._events[request_id] = asyncio.Event()
        self._pending[request_id] = []

    def get_chat_id(self, request_id: str) -> str | None:
        return self._chat_ids.get(request_id)

    def complete(self, request_id: str, status: str, reason: str | None = None) -> None:
        """Push a decision. Each call wakes one await_approval consumer."""
        if request_id not in self._pending:
            return
        self._pending[request_id].append({"status": status, "reason": reason})
        event = self._events.get(request_id)
        if event:
            event.set()
            self._events[request_id] = asyncio.Event()

    async def _await_one(self, request_id: str, timeout: float = 300) -> dict:
        """Wait for exactly one decision. Used by gather_decisions internally."""
        event = self._events.get(request_id)
        if not event:
            return {"status": "EXPIRED", "reason": "unknown request_id"}
        if self._pending.get(request_id):
            return self._pending[request_id].pop(0)
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return {"status": "EXPIRED", "reason": "timeout"}
        return (
            self._pending[request_id].pop(0)
            if self._pending.get(request_id)
            else {"status": "EXPIRED", "reason": "timeout"}
        )

    async def gather_decisions(
        self, request_id: str, expected_count: int, timeout: float = 300
    ) -> list[dict]:
        """Collect N decisions from N complete() calls. Returns list of {status, reason} dicts."""
        decisions: list[dict] = []
        for _ in range(expected_count):
            d = await self._await_one(request_id, timeout)
            decisions.append(d)
        # Cleanup
        self._events.pop(request_id, None)
        self._pending.pop(request_id, None)
        self._chat_ids.pop(request_id, None)
        return decisions
