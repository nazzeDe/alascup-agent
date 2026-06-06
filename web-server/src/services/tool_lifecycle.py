"""Centralized tool-call persistence lifecycle.

Replaces 4 scattered _persist_* functions across think/review/act/orchestrator
with 2 lifecycle methods: register() for INSERT, update() for UPDATE.
"""

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from src.models.tool import ApprovalStatus, ExecutionStatus, ServerName, ToolCall


class ToolCallLifecycle:
    """Centralized persistence for tool calls across the agent lifecycle."""

    def __init__(self, session_manager):
        self._sm = session_manager

    async def register(
        self,
        chat_id: UUID | None,
        pending: list[dict],
        pre_executed: list[dict],
        llm_trace_id: UUID | None,
    ) -> None:
        """INSERT newly discovered tool calls. Sets call_id on each dict IN-PLACE.

        This is a direct extraction of the current _persist_discovered_tools()
        from think.py.  All logic preserved verbatim.
        """
        if self._sm is None or chat_id is None:
            return

        now = datetime.now(timezone.utc)

        for tc in pending:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            args_str = fn.get("arguments", "{}")
            try:
                params = json.loads(args_str) if isinstance(args_str, str) else args_str
            except json.JSONDecodeError:
                params = {}
            tc_id_str = tc.get("id", str(uuid4()))
            try:
                msg_id = UUID(tc_id_str)
            except (ValueError, AttributeError):
                msg_id = uuid4()
            server_name = tc.get("server_name", "tool-server")

            call = ToolCall(
                name=name,
                server=ServerName(server_name) if server_name in {"tool-server", "rag-server"} else ServerName.TOOL_SERVER,
                description="",
                is_read_only=bool(tc.get("is_read_only", False)),
                is_rollbackable=bool(tc.get("is_rollbackable", False)),
                params_schema={},
                chat_id=chat_id,
                message_id=msg_id,
                params=params,
                request_id=None,
                approval_status=ApprovalStatus.PENDING,
                execution_status=ExecutionStatus.PENDING_APPROVAL,
                llm_trace_id=llm_trace_id,
                timestamp=now.isoformat(),
            )
            call_id = await self._sm.add_tool_call(chat_id, call)
            tc["call_id"] = call_id

        for pe in pre_executed:
            tc_id_str = pe.get("tool_call_id", str(uuid4()))
            try:
                msg_id = UUID(tc_id_str)
            except (ValueError, AttributeError):
                msg_id = uuid4()
            result = pe.get("result", {})
            exec_status_str = result.get("execution_status", "SUCCEEDED")

            call = ToolCall(
                name=pe.get("tool_name", ""),
                server=ServerName.TOOL_SERVER,
                description="",
                is_read_only=True,
                is_rollbackable=False,
                params_schema={},
                chat_id=chat_id,
                message_id=msg_id,
                params={},
                request_id=None,
                approval_status=ApprovalStatus.APPROVED,
                execution_status=ExecutionStatus(exec_status_str) if exec_status_str in {"SUCCEEDED", "FAILED", "RUNNING", "PENDING_APPROVAL"} else ExecutionStatus.SUCCEEDED,
                error=result.get("error"),
                llm_trace_id=llm_trace_id,
                timestamp=now.isoformat(),
            )
            pe_call_id = await self._sm.add_tool_call(chat_id, call)
            pe["call_id"] = pe_call_id
            # Pre-executed tools are already done; mark executed_at.
            await self._sm.update_tool_call(
                pe_call_id, chat_id,
                execution_status=call.execution_status,
                error=call.error,
            )

    async def update(
        self,
        chat_id: UUID | None,
        call_id,
        *,
        approval_status=None,
        execution_status=None,
        error=None,
        backup_ref=None,
        llm_trace_id=None,
    ) -> None:
        """UPDATE tool_call row after review/execution/approval.

        No-op if call_id is None or chat_id is None (matches current behavior).
        """
        if call_id is None or chat_id is None:
            return
        await self._sm.update_tool_call(
            call_id, chat_id,
            approval_status=approval_status,
            execution_status=execution_status,
            error=error,
            backup_ref=backup_ref,
            llm_trace_id=llm_trace_id,
        )
