# tool-server 测试

## 测试对象

| 模块 | 单元测试 | 集成测试 |
|------|---------|---------|
| 安全校验 | approval_status + request_id 格式校验 | — |
| classify_tool（分级查询）| 根据参数判定 isReadOnly/isRollbackable | — |
| ToolResult 构造 | 结构化输出 | — |
| 错误格式化 | SECURITY_VIOLATION 响应 | — |
| 感知工具 | 结果转换 | /proc、/sys、/var/log 模拟数据 |
| 操作工具 | bash sandbox + timeout | systemd mock |
| MCP Server | — | fastmcp test server 完整链路 |


## 测试用例

### CF-101 只读工具分类

| 前置 | tool-server 正常 |
| 输入 | classify_tool(tool_name="bash", params={command: "ls /tmp"}) |
| 预期 | isReadOnly=true, isRollbackable=true |

### CF-102 破坏性工具分类

| 前置 | tool-server 正常 |
| 输入 | classify_tool(tool_name="bash", params={command: "rm -rf /var/lib/mysql"}) |
| 预期 | isReadOnly=false, isRollbackable=false |

### CF-103 可回滚工具分类

| 前置 | tool-server 正常 |
| 输入 | classify_tool(tool_name="delete_temp_files", params={path: "/tmp"}) |
| 预期 | isReadOnly=false, isRollbackable=true |

### CF-104 感知工具分类

| 前置 | tool-server 正常 |
| 输入 | classify_tool(tool_name="get_cpu_info", params={}) |
| 预期 | isReadOnly=true, isRollbackable=true |

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
| 输入 | isReadOnly=false 且无 request_id |
| 预期 | 拒绝；返回 SECURITY_VIOLATION |

### SC-104 只读工具无需 request_id

| 前置 | tool-server 正常 |
| 输入 | isReadOnly=true，approval_status=APPROVED，无 request_id |
| 预期 | 校验通过，执行工具 |

### SC-105 沙箱写入只读挂载

| 前置 | sandbox_root 可写，/host/proc 只读挂载 |
| 输入 | bash command="echo x > /host/proc/test" |
| 预期 | 拒绝或 EACCES；不修改 /host/proc |

### SC-106 沙箱路径逃逸

| 前置 | sandbox_root=/tmp/sandbox |
| 输入 | bash command="cat /etc/passwd" |
| 预期 | 拒绝；目标路径不在 sandbox_root 内 |

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

### PR-105 read_logs

| 前置 | /var/log 模拟日志就绪 |
| 输入 | 调用 read_logs(path="/var/log/syslog", lines=50) |
| 预期 | 返回日志行列表 |

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

### OP-104 systemd 服务管理

| 前置 | systemd mock |
| 输入 | restart_service(name="nginx") |
| 预期 | 调用 systemd D-Bus；返回执行结果 |

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

### MCP Mock 工具

```python
get_cpu_info()       → {"cpu_percent": 85.0, "cores": 4}
get_disk_usage()     → {"disk_usage": 92, "largest_dir": "/tmp/logs"}
get_memory_info()    → {"mem_total_gb": 16, "mem_used_gb": 14}
get_process_info()   → {"pid": 12345, "name": "mysqld", "cpu_percent": 78}
delete_temp_files()  → {"deleted_bytes": "2.3GB"}
restart_service()    → {"execution_status": "FAILED", "error": "permission denied"}
```
