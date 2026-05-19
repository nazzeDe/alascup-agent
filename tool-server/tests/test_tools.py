from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


# ═══════════════════════════════════════════════════════════════════════════════
# classify_tool
# ═══════════════════════════════════════════════════════════════════════════════

class TestClassifyTool:
    def test_readonly_bash_ls(self):
        from src.security.classify import classify_tool
        result = classify_tool("bash", {"command": "ls /tmp"})
        assert result["isReadOnly"] is True
        assert result["isRollbackable"] is True

    def test_destructive_bash(self):
        from src.security.classify import classify_tool
        result = classify_tool("bash", {"command": "rm -rf /var/lib/mysql"})
        assert result["isReadOnly"] is False
        assert result["isRollbackable"] is False

    def test_rollbackable_delete_temp(self):
        from src.security.classify import classify_tool
        result = classify_tool("delete_temp_files", {"path": "/tmp"})
        assert result["isReadOnly"] is False
        assert result["isRollbackable"] is True

    def test_perception_readonly(self):
        from src.security.classify import classify_tool
        result = classify_tool("get_cpu_info", {})
        assert result["isReadOnly"] is True
        assert result["isRollbackable"] is True

    def test_systemd_restart_is_destructive(self):
        from src.security.classify import classify_tool
        result = classify_tool("manage_service", {"name": "nginx", "action": "restart"})
        assert result["isReadOnly"] is False

    def test_systemd_status_is_readonly(self):
        from src.security.classify import classify_tool
        result = classify_tool("manage_service", {"name": "nginx", "action": "status"})
        assert result["isReadOnly"] is True

    def test_network_perception_readonly(self):
        from src.security.classify import classify_tool
        result = classify_tool("get_network_info", {})
        assert result["isReadOnly"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# Security Validation
# ═══════════════════════════════════════════════════════════════════════════════

class TestSecurityValidation:
    rid = "550e8400-e29b-41d4-a716-446655440000"

    def test_rejects_pending_approval(self):
        from src.security.validate import validate_execution
        ok, err = validate_execution("PENDING", self.rid, False)
        assert ok is False
        assert err == "SECURITY_VIOLATION"

    def test_rejects_invalid_request_id(self):
        from src.security.validate import validate_execution
        ok, err = validate_execution("APPROVED", "not-a-uuid", False)
        assert ok is False
        assert err == "SECURITY_VIOLATION"

    def test_rejects_missing_request_id_destructive(self):
        from src.security.validate import validate_execution
        ok, err = validate_execution("APPROVED", "", False)
        assert ok is False
        assert err == "SECURITY_VIOLATION"

    def test_allows_readonly_without_request_id(self):
        from src.security.validate import validate_execution
        ok, err = validate_execution("APPROVED", "", True)
        assert ok is True
        assert err == ""

    def test_allows_approved_valid_request_id(self):
        from src.security.validate import validate_execution
        ok, err = validate_execution("APPROVED", self.rid, False)
        assert ok is True
        assert err == ""


# ═══════════════════════════════════════════════════════════════════════════════
# Perception Tools
# ═══════════════════════════════════════════════════════════════════════════════

class TestPerceptionTools:
    def test_get_cpu_info(self, config):
        from src.tools.perception.cpu import get_cpu_info
        result = get_cpu_info(config)
        assert result["cpu_percent"] >= 0
        assert result["cores"] >= 1
        assert isinstance(result["loadavg"], list)
        assert len(result["loadavg"]) == 3

    def test_get_memory_info(self, config):
        from src.tools.perception.memory import get_memory_info
        result = get_memory_info(config)
        assert result["mem_total_gb"] > 0
        assert result["mem_used_gb"] >= 0
        assert "swap_total_gb" in result

    def test_get_disk_usage(self, config):
        from src.tools.perception.disk import get_disk_usage
        result = get_disk_usage(config, path="/")
        assert "disk_usage_percent" in result
        assert result["disk_usage_percent"] >= 0

    def test_get_network_info(self, config):
        from src.tools.perception.network import get_network_info
        result = get_network_info(config)
        assert "interfaces" in result
        assert "iface_count" in result
        assert result["iface_count"] >= 0

    def test_get_process_list(self, config):
        from src.tools.perception.process import get_process_list
        result = get_process_list(config)
        assert isinstance(result, list)
        if result:
            p = result[0]
            assert "pid" in p
            assert "name" in p
            assert "cpu_time" in p
            assert "mem_mb" in p
            assert "status" in p

    def test_read_logs_nonexistent(self, config):
        from src.tools.perception.log_reader import read_logs
        result = read_logs(config, path="/nonexistent/log/path")
        assert result == []

    def test_read_logs_empty_path(self, config):
        from src.tools.perception.log_reader import read_logs
        result = read_logs(config, path="")
        assert result == []


# ═══════════════════════════════════════════════════════════════════════════════
# Operation Tools
# ═══════════════════════════════════════════════════════════════════════════════

class TestOperationTools:
    def test_bash_echo(self, sandbox):
        from src.tools.operation.bash import run_bash
        result = run_bash(sandbox, command="echo hello", timeout=5)
        assert result["execution_status"] == "SUCCEEDED"
        assert "hello" in result.get("stdout", "")

    def test_bash_invalid_command(self, sandbox):
        from src.tools.operation.bash import run_bash
        result = run_bash(sandbox, command="nonexistent_cmd_xyz", timeout=5)
        assert result["execution_status"] == "FAILED"
        assert result.get("returncode", 0) != 0

    def test_bash_timeout(self, sandbox):
        from src.tools.operation.bash import run_bash
        result = run_bash(sandbox, command="sleep 10", timeout=1)
        assert result["execution_status"] == "FAILED"

    def test_manage_service_no_args(self, config):
        from src.tools.operation.systemd import manage_service
        result = manage_service(config, name="", action="")
        assert result["execution_status"] == "FAILED"

    def test_manage_service_status(self, config):
        from src.tools.operation.systemd import manage_service
        result = manage_service(config, name="nonexistent-service-xyz", action="status")
        assert "execution_status" in result


# ═══════════════════════════════════════════════════════════════════════════════
# Tool Registry
# ═══════════════════════════════════════════════════════════════════════════════

class TestToolRegistry:
    def test_register_and_retrieve(self, config):
        from src.tools.registry import register, get_tool, clear_registry, ToolMeta
        clear_registry()

        def dummy(config, **kw):
            return {"ok": True}

        register(ToolMeta(
            name="dummy",
            description="test tool",
            is_read_only=True,
            input_schema={},
            fn=dummy,
        ))

        meta = get_tool("dummy")
        assert meta is not None
        assert meta.name == "dummy"
        assert meta.is_read_only is True

        result = meta.fn(config)
        assert result == {"ok": True}
        clear_registry()

    def test_get_nonexistent(self):
        from src.tools.registry import get_tool, clear_registry
        clear_registry()
        assert get_tool("nonexistent") is None


# ═══════════════════════════════════════════════════════════════════════════════
# Cache
# ═══════════════════════════════════════════════════════════════════════════════

class TestCache:
    def test_put_and_get(self, cache):
        cache.put("req-1", {"result": "ok"})
        assert cache.get("req-1") == {"result": "ok"}

    def test_miss(self, cache):
        assert cache.get("nonexistent") is None

    def test_evict(self, cache):
        cache.put("req-2", {"result": "ok"})
        cache.evict("req-2")
        assert cache.get("req-2") is None


# ═══════════════════════════════════════════════════════════════════════════════
# Handle Execute Tool (dispatch pipeline)
# ═══════════════════════════════════════════════════════════════════════════════

class TestHandleExecuteTool:
    rid = "550e8400-e29b-41d4-a716-446655440000"

    @pytest.fixture(autouse=True)
    def _setup_registry(self, config):
        from src.main import _register_tools
        _register_tools(config)

    def test_security_rejects_pending(self, config, cache):
        from src.handlers.dispatch import handle_execute_tool
        result = handle_execute_tool(
            tool_name="get_cpu_info", chat_id="c1", message_id="m1",
            params={}, request_id=self.rid, approval_status="PENDING",
            config=config, cache=cache,
        )
        assert "error" in result

    def test_execute_tool_not_found(self, config, cache):
        from src.handlers.dispatch import handle_execute_tool
        from src.tools.registry import clear_registry
        clear_registry()
        result = handle_execute_tool(
            tool_name="nonexistent_tool", chat_id="c1", message_id="m1",
            params={}, request_id=self.rid, approval_status="APPROVED",
            config=config, cache=cache,
        )
        assert "error" in result

    def test_execute_readonly_tool_without_request_id(self, config, cache):
        from src.handlers.dispatch import handle_execute_tool
        result = handle_execute_tool(
            tool_name="get_cpu_info", chat_id="c1", message_id="m1",
            params={}, request_id="", approval_status="APPROVED",
            config=config, cache=cache,
        )
        assert "error" not in result

    def test_cache_hit(self, config, cache):
        from src.handlers.dispatch import handle_execute_tool
        cache.put(self.rid, {"cached": True})
        result = handle_execute_tool(
            tool_name="get_cpu_info", chat_id="c1", message_id="m1",
            params={}, request_id=self.rid, approval_status="APPROVED",
            config=config, cache=cache,
        )
        assert result == {"cached": True}


# ═══════════════════════════════════════════════════════════════════════════════
# Server creation (no transport)
# ═══════════════════════════════════════════════════════════════════════════════

class TestServerCreation:
    def test_create_server(self, config):
        from src.main import create_server
        from src.tools.registry import clear_registry
        clear_registry()
        server = create_server(config)
        assert server is not None
        assert server.name == "tool-server"
