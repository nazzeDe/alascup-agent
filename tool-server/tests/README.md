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
| 伴生分类工具 | bash 只读免审批分类、fail-closed | `bash_classify` MCP 调用 |
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

### CF-103 敏感读取需要审批

| 前置 | tool-server 正常 |
| 输入 | `bash_classify(command="cat /etc/shadow")`、`bash_classify(command="cat ~/.ssh/id_rsa")`、`bash_classify(command="cat /proc/1/environ")` |
| 预期 | `{"safe": false}` |

### CF-104 运维观测命令可免审批

| 前置 | tool-server 正常 |
| 输入 | `bash_classify(command="ss -tulpn \| grep nginx")`、`bash_classify(command="systemctl --no-pager status nginx")` |
| 预期 | `{"safe": true}` |

### CF-105 执行面与网络请求需要审批

| 前置 | tool-server 正常 |
| 输入 | `bash_classify(command="bash -c 'whoami'")`、`bash_classify(command="curl -I https://example.com")`、`bash_classify(command="find /tmp -name '*.log' -exec cat {} \\;")` |
| 预期 | `{"safe": false}` |

### CF-106 可写 flag 与危险子命令需要审批

| 前置 | tool-server 正常 |
| 输入 | `bash_classify(command="sed -i 's/a/b/' file.txt")`、`bash_classify(command="git reset --hard")`、`bash_classify(command="systemctl restart nginx")` |
| 预期 | `{"safe": false}` |

### CF-107 伴生分类工具通过 MCP 调用

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

### MCP-103A 配置共享密钥时 execute_tool 必须携带 auth_token

| 前置 | `ToolServerConfig(shared_secret="secret")` |
| 输入 | `execute_tool` 不携带或携带错误 `auth_token` |
| 预期 | `SECURITY_VIOLATION`；携带匹配 token 时才执行 |

### MCP-104 直接调用可变工具被拒绝

| 前置 | FastMCP server 已创建 |
| 输入 | 直接 `call_tool("bash", {"command": "echo bypass"})`，不经过 `execute_tool` |
| 预期 | `SECURITY_VIOLATION`，命令不执行 |

### SC-101 approval_status 校验

| 前置 | tool-server 正常 |
| 输入 | web-server 发 approval_status=PENDING |
| 预期 | 拒绝；返回 error code=403 message=SECURITY_VIOLATION |

### SC-100 auth_token 校验

| 前置 | 配置 `TOOLSERVER_SHARED_SECRET` |
| 输入 | `auth_token` 缺失或不匹配 |
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

### SC-105 request_id 幂等缓存绑定上下文

| 前置 | 同一 `request_id` 已执行过一次 |
| 输入 | 使用同一 `request_id` 调用不同工具或同工具不同参数 |
| 预期 | 返回 `SECURITY_VIOLATION`，不返回旧缓存结果 |

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
| 输入 | 调用 get_process_list，默认 `top_n=50` |
| 预期 | 返回 `processes`、`limit`、`max_limit`、`total_seen`、`truncated`；进程按 CPU/内存排序，条目含 pid + name + cpu_time + mem_mb + status |

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

### EBPF-101 订阅缺少探针变体

| 前置 | 当前内核无匹配探针脚本 |
| 输入 | 启动 `BpftraceDaemon("missing.bt")` |
| 预期 | `permanent_failure=true`，`probe_status=permanent_failure` |

### EBPF-102 订阅停止状态

| 前置 | eBPF daemon 已持有运行中进程 |
| 输入 | 调用 `stop()` |
| 预期 | `probe_status=stopped` |

### EBPF-103 订阅重启清除停止状态

| 前置 | daemon 曾进入 `stopped` 状态，探针脚本可解析 |
| 输入 | 再次调用 `start()` |
| 预期 | 清除旧停止状态；成功拉起进程后 `probe_status=running` |

### EBPF-104 订阅批量启动幂等

| 前置 | 指定探针 daemon 已运行 |
| 输入 | 重复调用 `SubscriptionManager.start_all(enabled=[...])` |
| 预期 | 不重复 spawn 同名运行中 daemon |

### EBPF-105 单探针启动失败不阻断其他探针

| 前置 | 多个探针同时启动，其中一个 spawn 抛异常 |
| 输入 | 调用 `start_all(enabled=["bad.bt", "good.bt"])` |
| 预期 | 失败探针注册为 `permanent_failure`，其他探针继续启动 |

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
