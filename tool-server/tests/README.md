# tool-server 测试

tool-server 测试重点保护工具执行层的安全边界和 MCP 协议链路。单元测试验证分类、校验、缓存、工具结果和本地执行；集成测试使用 `fastmcp.Client(server)` 验证真实 MCP 工具发现和 `call_tool` 调用。

## 执行命令

```bash
cd tool-server
uv run pytest tests/ -m unit -v
uv run pytest tests/ -m integration -v
uv run pytest tests/ --cov=src --cov-branch --cov-report=term-missing
```

## 测试对象

| 模块 | 单元测试 | 集成测试 |
|------|---------|---------|
| 安全校验 | approval_status + request_id 格式校验 | — |
| 伴生分类工具 | bash AST 分类、fail-closed | `bash_classify` MCP 调用 |
| ToolResult 构造 | 结构化输出 | — |
| 错误格式化 | SECURITY_VIOLATION 响应 | — |
| 感知工具 | CPU、内存、磁盘、网络、进程结果转换 | — |
| 操作工具 | bash sandbox、timeout、host_exec 参数 | `execute_tool` 调用 approved bash |
| MCP Server | 工具注册契约、hidden/meta 校验 | 工具发现、结构化结果、安全拒绝 |


## 测试用例

### CF-101 只读工具分类

| 前置 | tool-server 正常 |
| 输入 | `bash_classify(command="ls /tmp")` |
| 预期 | `{"safe": true}` |

### CF-102 破坏性工具分类

| 前置 | tool-server 正常 |
| 输入 | `bash_classify(command="rm -rf /var/lib/mysql")` |
| 预期 | `{"safe": false}` |

### CF-103 伴生分类工具通过 MCP 调用

| 前置 | FastMCP server 已创建 |
| 输入 | `Client(server).call_tool("bash_classify", {"command": "ls /tmp"})` |
| 预期 | structured content 为 `{"safe": true}` |

### MCP-101 工具发现

| 前置 | FastMCP server 已创建 |
| 输入 | `Client(server).list_tools()` |
| 预期 | 包含 `get_cpu_info`、`bash`、`bash_classify`、`execute_tool` |

### MCP-102 未审批执行被拒绝

| 前置 | FastMCP server 已创建 |
| 输入 | `execute_tool` 携带 `approval_status=PENDING` |
| 预期 | `execution_status=FAILED`，error message 为 `SECURITY_VIOLATION` |

### MCP-103 已审批执行成功

| 前置 | FastMCP server 已创建，sandbox 指向临时目录 |
| 输入 | `execute_tool` 执行 `bash command="echo mcp-ok"`，approval 为 `APPROVED` |
| 预期 | `execution_status=SUCCEEDED`，stdout 为 `mcp-ok\n` |

### SC-101 approval_status 校验

| 前置 | tool-server 正常 |
| 输入 | web-server 发 approval_status=PENDING |
| 预期 | 拒绝；返回 error code=403 message=SECURITY_VIOLATION |

### SC-102 request_id 格式校验

| 前置 | tool-server 正常 |
| 输入 | request_id 为空或非 UUID 格式 |
| 预期 | 拒绝；返回 SECURITY_VIOLATION |

### SC-103 高风险工具必须携带 request_id

| 前置 | tool-server 正常 |
| 输入 | `is_read_only=false` 且无 `request_id` |
| 预期 | 拒绝；返回 SECURITY_VIOLATION |

### SC-104 只读工具无需 request_id

| 前置 | tool-server 正常 |
| 输入 | `is_read_only=true`，`approval_status=APPROVED`，无 `request_id` |
| 预期 | 校验通过，执行工具 |

### PR-101 get_cpu_info

| 前置 | /proc 模拟数据就绪 |
| 输入 | 调用 get_cpu_info |
| 预期 | 返回 cpu_percent + cores + model_name |

### PR-102 get_memory_info

| 前置 | /proc/meminfo 模拟数据就绪 |
| 输入 | 调用 get_memory_info |
| 预期 | 返回 mem_total_gb + mem_used_gb + swap_total_gb |

### PR-103 get_disk_usage

| 前置 | /sys 模拟数据就绪 |
| 输入 | 调用 get_disk_usage |
| 预期 | 返回 disk_usage_percent + largest_dir |

### PR-104 get_process_list

| 前置 | /proc 进程目录模拟 |
| 输入 | 调用 get_process_list |
| 预期 | 返回进程列表含 pid + name + cpu_percent |

### OP-101 bash 执行成功

| 前置 | sandbox_root 可写 |
| 输入 | bash command="echo hello" |
| 预期 | SUCCEEDED，output.stdout="hello\n" |

### OP-102 bash 执行超时

| 前置 | sandbox_root 可写 |
| 输入 | bash command="sleep 60"，timeout=1 |
| 预期 | TIMEOUT，进程被 SIGKILL |

### OP-103 bash 执行失败

| 前置 | sandbox_root 可写 |
| 输入 | bash command="nonexistent_cmd" |
| 预期 | FAILED，output 含 returncode 和 stderr |

## Mock 配置

### 宿主机模拟数据

测试时注入临时目录：

```python
# 生产 /proc，测试 /tmp/test-proc
ToolServerConfig(proc_path="/tmp/test-proc")
```

**`/tmp/test-proc/cpuinfo`**：
```
processor       : 0
vendor_id       : Loongson
model name      : Loongson-3A5000
```

**`/tmp/test-proc/meminfo`**：
```
MemTotal:       16384000 kB
MemFree:         2048000 kB
SwapTotal:       8192000 kB
```

**`/tmp/test-proc/loadavg`**（高负载）：
```
4.52 3.81 2.90 5/1024 18420
```

**`/tmp/test-proc/loadavg`**（正常）：
```
0.45 0.32 0.28 2/512 12345
```

**`/tmp/test-log/syslog`**（含僵尸进程线索）：
```
May  7 10:23:15 server1 kernel: task mysqld:12345 blocked for more than 120 seconds.
May  7 10:23:30 server1 systemd[1]: mysql.service: Main process exited
May  7 10:23:35 server1 systemd[1]: mysql.service: Scheduled restart job
```

### MCP 集成替身

`test_mcp_integration.py` 使用内存 FastMCP server，不占用端口。测试中用 `_DummyEbpfRuntime` 替代 eBPF 后台 daemon，避免依赖内核权限，同时保留 MCP 工具注册、发现和调用路径。
