from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


class TestConfigLoading:
    def test_load_config_applies_file_and_env_overrides(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.config import load_config

        config_file = tmp_path / "config.json"
        config_file.write_text(
            '{"cache_ttl": 10, "bash_timeout": 20}', encoding="utf-8"
        )
        monkeypatch.setenv("TOOLSERVER_CONFIG_PATH", str(config_file))
        monkeypatch.setenv("TOOLSERVER_CACHE_TTL", "30")
        monkeypatch.setenv("TOOLSERVER_HOST_EXEC", "chroot")
        monkeypatch.setenv("TOOLSERVER_HOST", "127.0.0.2")

        config = load_config()

        assert config.cache_ttl == 30
        assert config.bash_timeout == 20
        assert config.host_exec == "chroot"
        assert config.host == "127.0.0.2"

    def test_load_config_uses_defaults_when_file_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.config import load_config

        monkeypatch.setenv("TOOLSERVER_CONFIG_PATH", str(tmp_path / "missing.json"))

        config = load_config()

        assert config.port == 11451
        assert config.postgres_dsn is None

    def test_non_loopback_host_requires_shared_secret(self) -> None:
        from pydantic import ValidationError

        from src.config import ToolServerConfig

        all_interfaces = ".".join(("0", "0", "0", "0"))
        with pytest.raises(ValidationError, match="TOOLSERVER_SHARED_SECRET"):
            ToolServerConfig(host=all_interfaces)

    def test_env_non_loopback_host_requires_shared_secret(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pydantic import ValidationError

        from src.config import load_config

        monkeypatch.setenv("TOOLSERVER_CONFIG_PATH", str(tmp_path / "missing.json"))
        all_interfaces = ".".join(("0", "0", "0", "0"))
        monkeypatch.setenv("TOOLSERVER_HOST", all_interfaces)

        with pytest.raises(ValidationError, match="TOOLSERVER_SHARED_SECRET"):
            load_config()

    def test_non_loopback_host_allows_shared_secret(self) -> None:
        from src.config import ToolServerConfig

        all_interfaces = ".".join(("0", "0", "0", "0"))
        shared_token = "shared-" + "token"
        config = ToolServerConfig(host=all_interfaces, shared_secret=shared_token)

        assert config.shared_secret == shared_token


class TestPostgresEdges:
    @pytest.mark.asyncio
    async def test_schema_rejects_invalid_identifier_before_connect(
        self, config
    ) -> None:
        from src.tools.data.postgres import get_postgres_schema

        result = await get_postgres_schema(
            config.model_copy(update={"postgres_dsn": "postgresql://db"}), "bad-name"
        )

        assert result == {"error": "invalid schema"}

    @pytest.mark.asyncio
    async def test_schema_groups_columns_by_table(
        self, config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.tools.data import postgres

        async def fake_fetch(_config, _sql, *_args, timeout=None):
            assert timeout is None
            return [
                {
                    "table_schema": "public",
                    "table_name": "sessions",
                    "column_name": "id",
                    "data_type": "uuid",
                    "is_nullable": "NO",
                    "column_default": None,
                },
                {
                    "table_schema": "public",
                    "table_name": "sessions",
                    "column_name": "title",
                    "data_type": "text",
                    "is_nullable": "YES",
                    "column_default": None,
                },
            ]

        monkeypatch.setattr(postgres, "_fetch", fake_fetch)

        result = await postgres.get_postgres_schema(
            config.model_copy(update={"postgres_dsn": "postgresql://db"})
        )

        assert result["table_count"] == 1
        assert result["tables"][0]["columns"] == [
            {"name": "id", "data_type": "uuid", "nullable": False, "default": None},
            {"name": "title", "data_type": "text", "nullable": True, "default": None},
        ]

    @pytest.mark.asyncio
    async def test_readonly_query_limits_rows_and_jsonifies_values(
        self, config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.tools.data import postgres

        async def fake_fetch_limited(_config, sql, limit, timeout=None):
            assert sql == "select now() as ts"
            assert limit == 2
            assert timeout == 5.0
            return [
                {
                    "ts": datetime(2026, 7, 5, 8, 0),
                    "items": [1, datetime(2026, 7, 5, 8, 1)],
                },
                {"ts": datetime(2026, 7, 5, 8, 2), "items": []},
            ]

        monkeypatch.setattr(postgres, "_fetch_limited", fake_fetch_limited)

        result = await postgres.postgres_readonly_query(
            config.model_copy(
                update={"postgres_dsn": "postgresql://db", "postgres_max_rows": 5}
            ),
            "select now() as ts;",
            max_rows=1,
        )

        assert result == {
            "columns": ["ts", "items"],
            "rows": [
                {"ts": "2026-07-05T08:00:00", "items": [1, "2026-07-05T08:01:00"]}
            ],
            "row_count": 1,
            "truncated": True,
            "max_rows": 1,
        }

    def test_sql_scrubber_handles_comments_and_quoted_identifiers(self) -> None:
        from src.tools.data.postgres import _validate_readonly_sql

        assert (
            _validate_readonly_sql('select "drop" from audit -- update ignored\n')[0]
            is True
        )
        assert _validate_readonly_sql("select 1 /* delete ignored */")[0] is True
        assert _validate_readonly_sql("select 1; -- trailing comment")[0] is True

    @pytest.mark.parametrize(
        "sql",
        [
            "select pg_read_file('/etc/passwd')",
            "select pg_read_binary_file('/etc/passwd')",
            "select pg_ls_dir('/')",
            "select pg_stat_file('/etc/passwd')",
            "select lo_import('/etc/passwd')",
            "select \"pg_read_file\"('/etc/passwd')",
            "select pg_catalog.\"pg_read_file\"('/etc/passwd')",
            "select \"lo_import\"('/etc/passwd')",
            "select pg_ls_logicalmapdir()",
            "select pg_ls_logicalsnapdir()",
            "select pg_ls_replslotdir('slot')",
            "select pg_ls_summariesdir()",
            "select pg_ls_archive_statusdir()",
        ],
    )
    def test_readonly_sql_rejects_privileged_file_functions(self, sql: str) -> None:
        from src.tools.data.postgres import _validate_readonly_sql

        ok, error = _validate_readonly_sql(sql)

        assert ok is False
        assert "file and program access" in error

    @pytest.mark.parametrize(
        "sql",
        [
            "select pg_terminate_backend(pid) from pg_stat_activity",
            "select pg_cancel_backend(pid) from pg_stat_activity",
            "select pg_reload_conf()",
            "select dblink_exec('dbname=postgres', 'create table x(y int)')",
            "select lo_unlink(123)",
            "select nextval('audit_id_seq')",
            "select setval('audit_id_seq', 10)",
            "select pg_notify('events', 'changed')",
            "select pg_advisory_lock(1)",
        ],
    )
    def test_readonly_sql_rejects_side_effect_functions(self, sql: str) -> None:
        from src.tools.data.postgres import _validate_readonly_sql

        ok, error = _validate_readonly_sql(sql)

        assert ok is False
        assert "side-effecting" in error


class TestBashClassifierAdversarial:
    @pytest.mark.parametrize(
        "command",
        [
            "awk '{ print > \"/tmp/out\" }' /tmp/in",
            'awk \'{ printf "x" > "/tmp/out" }\' /tmp/in',
            "awk '{ system(\"id\") }' /tmp/in",
            "sed -n 's/a/b/w /tmp/out' /tmp/in",
            "sed -n '1 w /tmp/out' /tmp/in",
            "sed -n '1w/tmp/out' /tmp/in",
            "sed -n '1W /tmp/out' /tmp/in",
            "sed -f ~/.ssh/id_rsa /tmp/in",
            "sed -n '1 r /etc/shadow' /tmp/in",
            "sed -n 'e printf SED_E_CMD_OK' /tmp/in",
            "sed -n 's/x/printf SED_EXEC_OK/ep' /tmp/in",
            "sed -n '/x/e id' /tmp/in",
            "sed -n '/x/w /tmp/out' /tmp/in",
            "sed -n '/x/r /etc/shadow' /tmp/in",
        ],
    )
    def test_write_capable_text_programs_require_approval(self, command: str) -> None:
        from src.security.bash_classify import classify_bash

        assert classify_bash(command) == {"safe": False}

    def test_path_qualified_allowlisted_binary_requires_approval(self) -> None:
        from src.security.bash_classify import classify_bash

        assert classify_bash("/opt/ls") == {"safe": False}

    @pytest.mark.parametrize(
        "command",
        [
            "cat /etc/../etc/shadow",
            "cat /home/user/../user/.ssh/id_rsa",
            "cat ~root/.ssh/id_rsa",
            "cat .ssh/id_rsa",
            "cat ../.ssh/id_rsa",
            "cat .aws/credentials",
            "cat .env",
            "cat /tmp/.env",
            "cat /tmp/client.pem",
            "cat /tmp/secrets/token.txt",
            "git show HEAD:/etc/shadow",
            "git show HEAD:secrets/token.txt",
            "git show HEAD:.env",
        ],
    )
    def test_sensitive_paths_cannot_be_bypassed_with_normalization(
        self, command: str
    ) -> None:
        from src.security.bash_classify import classify_bash

        assert classify_bash(command) == {"safe": False}

    @pytest.mark.parametrize(
        "command",
        [
            "ps eww",
            "ps auxe",
            "ps auxwwwe",
            "ps ef",
        ],
    )
    def test_ps_environment_output_requires_approval(self, command: str) -> None:
        from src.security.bash_classify import classify_bash

        assert classify_bash(command) == {"safe": False}

    @pytest.mark.parametrize(
        "command",
        [
            "ip link set eth0 down",
            "ip -j link set eth0 down",
            "ip addr add 127.0.0.2/8 dev lo",
            "ip route delete default",
            "ip netns exec prod ip addr",
        ],
    )
    def test_ip_mutating_operations_require_approval(self, command: str) -> None:
        from src.security.bash_classify import classify_bash

        assert classify_bash(command) == {"safe": False}

    @pytest.mark.parametrize(
        "command",
        [
            "echo $POSTGRES_DSN",
            "printf %s $AWS_SECRET_ACCESS_KEY",
            "cat $POSTGRES_DSN",
        ],
    )
    def test_shell_expansion_requires_approval(self, command: str) -> None:
        from src.security.bash_classify import classify_bash

        assert classify_bash(command) == {"safe": False}


class TestBashExecutionEdges:
    def test_default_sandbox_creation_failure_returns_failed_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.config import ToolServerConfig
        from src.tools.operation import bash

        def deny_makedirs(_path, **_kwargs):
            raise PermissionError("denied")

        monkeypatch.setattr(bash._os, "makedirs", deny_makedirs)

        from src.security.execution_context import controlled_execution

        with controlled_execution():
            result = bash.run_bash(ToolServerConfig(), command="echo hello")

        assert result["execution_status"] == "FAILED"
        assert result["stderr"] == "denied"

    def test_timeout_kills_process_group(self, sandbox, monkeypatch: pytest.MonkeyPatch):
        from subprocess import TimeoutExpired

        from src.security.execution_context import controlled_execution
        from src.tools.operation import bash

        calls: list[tuple[str, int]] = []

        class HangingProcess:
            pid = 12345
            returncode = None

            def communicate(self, timeout=None):
                if timeout is None:
                    return "", ""
                raise TimeoutExpired(cmd=["bash"], timeout=timeout)

            def wait(self, timeout=None):
                self.returncode = -15
                return self.returncode

            def kill(self):
                calls.append(("kill", self.pid))

        monkeypatch.setattr(bash.subprocess, "Popen", lambda *_args, **_kwargs: HangingProcess())
        def record_killpg(pid: int, _signal: int) -> None:
            calls.append(("killpg", pid))

        monkeypatch.setattr(bash._os, "killpg", record_killpg)

        with controlled_execution():
            result = bash.run_bash(sandbox, command="sleep 600 & wait", timeout=1)

        assert result["execution_status"] == "FAILED"
        assert ("killpg", 12345) in calls


class TestExecutionLifecycleLogging:
    @pytest.mark.asyncio
    async def test_tool_execute_log_redacts_param_values(self, cache):
        from fastmcp import FastMCP
        from loguru import logger

        from src.handlers.dispatch import handle_execute_tool

        lines: list[str] = []
        sink_id = logger.add(lines.append, format="{message}")
        server = FastMCP(name="logging-test-tool-server")

        @server.tool(name="echo", meta={"is_read_only": True})
        def echo(command: str, token: str) -> dict:
            return {"ok": bool(command and token)}

        try:
            await handle_execute_tool(
                server=server,
                tool_name="echo",
                chat_id="c1",
                params={"command": "echo sk-secret", "token": "secret-token"},
                request_id="",
                approval_status="APPROVED",
                cache=cache,
            )
        finally:
            logger.remove(sink_id)

        log_text = "\n".join(lines)
        assert "sk-secret" not in log_text
        assert "secret-token" not in log_text
        assert "params={command:<redacted:" in log_text


class _FakeProcess:
    def __init__(
        self,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        hang: bool = False,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.hang = hang
        self.terminated = False
        self.killed = False

    async def communicate(self):
        if self.hang:
            await asyncio.sleep(1)
        return self._stdout, self._stderr

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.returncode = -15 if self.terminated else self.returncode
        return self.returncode


def _fake_subprocess_factory(proc: _FakeProcess):
    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return proc

    return fake_create_subprocess_exec


class TestBpftraceRunnerEdges:
    def test_on_demand_timeout_uses_script_window_with_grace(self) -> None:
        from src.tools.perception.ebpf.runner import on_demand_timeout

        assert on_demand_timeout("syscount.bt", requested_duration=3) == 15.0
        assert on_demand_timeout("syscount.bt", requested_duration=20) == 25.0

    @pytest.mark.asyncio
    async def test_run_on_demand_parses_json_and_skips_console_lines(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from src.tools.perception.ebpf import runner

        script = tmp_path / "syscount.bt"
        script.write_text("// probe", encoding="utf-8")
        proc = _FakeProcess(
            stdout=b'Attaching 1 probe...\n{"type":"map","data":{"open":2}}\nnot json\n',
            returncode=0,
        )
        monkeypatch.setattr(
            runner.asyncio, "create_subprocess_exec", _fake_subprocess_factory(proc)
        )

        result = await runner.run_on_demand(
            "syscount.bt", timeout=1, resolve_fn=lambda _name: script
        )

        assert result == [{"type": "map", "data": {"open": 2}}]

    @pytest.mark.asyncio
    async def test_run_on_demand_reports_nonzero_exit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.tools.perception.ebpf import runner

        script = tmp_path / "bad.bt"
        script.write_text("// probe", encoding="utf-8")
        proc = _FakeProcess(stderr=b"syntax error", returncode=2)
        monkeypatch.setattr(
            runner.asyncio, "create_subprocess_exec", _fake_subprocess_factory(proc)
        )

        result = await runner.run_on_demand(
            "bad.bt", timeout=1, resolve_fn=lambda _name: script
        )

        assert result == [
            {"error": "syntax error", "script": "bad.bt", "returncode": 2}
        ]

    @pytest.mark.asyncio
    async def test_run_on_demand_terminates_on_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.tools.perception.ebpf import runner

        script = tmp_path / "slow.bt"
        script.write_text("// probe", encoding="utf-8")
        proc = _FakeProcess(hang=True)
        monkeypatch.setattr(
            runner.asyncio, "create_subprocess_exec", _fake_subprocess_factory(proc)
        )

        result = await runner.run_on_demand(
            "slow.bt", timeout=0.001, resolve_fn=lambda _name: script
        )

        assert result == [{"error": "timeout", "script": "slow.bt", "timeout_s": 0.001}]
        assert proc.terminated is True

    @pytest.mark.asyncio
    async def test_run_on_demand_terminates_on_cancellation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.tools.perception.ebpf import runner

        script = tmp_path / "slow.bt"
        script.write_text("// probe", encoding="utf-8")
        proc = _FakeProcess(hang=True)
        monkeypatch.setattr(
            runner.asyncio, "create_subprocess_exec", _fake_subprocess_factory(proc)
        )

        task = asyncio.create_task(
            runner.run_on_demand("slow.bt", timeout=10, resolve_fn=lambda _name: script)
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert proc.terminated is True

    @pytest.mark.asyncio
    async def test_run_on_demand_reports_missing_probe(self) -> None:
        from src.tools.perception.ebpf.runner import run_on_demand

        result = await run_on_demand("missing.bt", resolve_fn=lambda _name: None)

        assert result == [
            {
                "error": "probe script not found (no variant for this kernel): missing.bt",
                "script": "missing.bt",
            }
        ]


class _AsyncLines:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = iter(lines)

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self._lines)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _ReadableBytes:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self) -> bytes:
        return self._data


class TestBpftraceSubscriptionEdges:
    def test_start_without_probe_variant_sets_permanent_failure(self) -> None:
        from src.tools.perception.ebpf.subscription import BpftraceDaemon

        daemon = BpftraceDaemon("missing.bt", resolve_fn=lambda _name: None)

        asyncio.run(daemon.start())

        assert daemon.permanent_failure is True
        assert daemon.status == "permanent_failure"

    @pytest.mark.asyncio
    async def test_consume_keeps_ring_buffer_latest_events(self) -> None:
        from src.tools.perception.ebpf.subscription import BpftraceDaemon

        daemon = BpftraceDaemon(
            "execsnoop.bt", buffer_size=1, resolve_fn=lambda _name: Path("execsnoop.bt")
        )
        daemon._proc = type(
            "Proc",
            (),
            {
                "stdout": _AsyncLines(
                    [b'{"pid": 1}\n', b"not-json\n", b'{"pid": 2}\n']
                ),
                "stderr": _ReadableBytes(b""),
                "returncode": None,
            },
        )()

        await daemon._consume()

        assert daemon.drain() == [{"pid": 2}]

    @pytest.mark.asyncio
    async def test_collect_stderr_marks_fatal_errors(self) -> None:
        from src.tools.perception.ebpf.subscription import BpftraceDaemon

        daemon = BpftraceDaemon(
            "execsnoop.bt", resolve_fn=lambda _name: Path("execsnoop.bt")
        )
        daemon._proc = type(
            "Proc",
            (),
            {
                "stdout": _AsyncLines([]),
                "stderr": _ReadableBytes(b"permission denied"),
                "returncode": None,
            },
        )()

        await daemon._collect_stderr()

        assert daemon.permanent_failure is True

    @pytest.mark.asyncio
    async def test_stop_reports_stopped_status_after_process_exit(self) -> None:
        from src.tools.perception.ebpf.subscription import BpftraceDaemon

        daemon = BpftraceDaemon(
            "execsnoop.bt", resolve_fn=lambda _name: Path("execsnoop.bt")
        )
        daemon._proc = _FakeProcess(returncode=None)

        await daemon.stop()

        assert daemon.status == "stopped"

    @pytest.mark.asyncio
    async def test_start_clears_previous_stopped_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.tools.perception.ebpf import subscription
        from src.tools.perception.ebpf.subscription import BpftraceDaemon

        script = tmp_path / "execsnoop.bt"
        script.write_text("// probe", encoding="utf-8")
        proc = _FakeProcess(returncode=None)
        proc.stdout = _AsyncLines([])
        proc.stderr = _ReadableBytes(b"")
        monkeypatch.setattr(
            subscription.asyncio,
            "create_subprocess_exec",
            _fake_subprocess_factory(proc),
        )

        daemon = BpftraceDaemon("execsnoop.bt", resolve_fn=lambda _name: script)
        daemon._stopped = True

        await daemon.start()

        assert daemon.status == "running"
        await daemon.stop()

    @pytest.mark.asyncio
    async def test_start_all_is_idempotent_for_running_daemons(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.tools.perception.ebpf import subscription
        from src.tools.perception.ebpf.subscription import SubscriptionManager

        script = tmp_path / "execsnoop.bt"
        script.write_text("// probe", encoding="utf-8")
        proc = _FakeProcess(returncode=None)
        proc.stdout = _AsyncLines([])
        proc.stderr = _ReadableBytes(b"")
        calls = 0

        async def fake_create_subprocess_exec(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            return proc

        monkeypatch.setattr(
            subscription.asyncio,
            "create_subprocess_exec",
            fake_create_subprocess_exec,
        )
        manager = SubscriptionManager(resolve_fn=lambda _name: script)

        await manager.start_all(enabled=["execsnoop.bt"])
        await manager.start_all(enabled=["execsnoop.bt"])

        assert calls == 1
        assert manager.probe_status("execsnoop.bt") == "running"
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_start_all_keeps_failed_probe_registered_and_continues(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.tools.perception.ebpf import subscription
        from src.tools.perception.ebpf.subscription import SubscriptionManager

        bad_script = tmp_path / "bad.bt"
        good_script = tmp_path / "good.bt"
        bad_script.write_text("// bad", encoding="utf-8")
        good_script.write_text("// good", encoding="utf-8")
        good_proc = _FakeProcess(returncode=None)
        good_proc.stdout = _AsyncLines([])
        good_proc.stderr = _ReadableBytes(b"")
        calls = 0

        async def fake_create_subprocess_exec(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("spawn failed")
            return good_proc

        monkeypatch.setattr(
            subscription.asyncio,
            "create_subprocess_exec",
            fake_create_subprocess_exec,
        )
        manager = SubscriptionManager(
            resolve_fn=lambda name: bad_script if name == "bad.bt" else good_script
        )

        await manager.start_all(enabled=["bad.bt", "good.bt"])

        assert manager.probe_status("bad.bt") == "permanent_failure"
        assert manager.probe_status("good.bt") == "running"
        await manager.shutdown()
