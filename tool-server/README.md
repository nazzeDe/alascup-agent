# tool-server

工具执行层。提供系统感知工具和运维操作工具，通过 fastmcp 暴露 MCP 接口。

## 职责

- 暴露系统感知 MCP 工具（CPU/内存/磁盘/网络/进程/日志采集）
- 暴露运维操作 MCP 工具（bash、systemd）
- 根据工具参数动态判定操作安全分级（classify_tool：isReadOnly / isRollbackable）
- 执行前二次安全校验（验证 approval_status + request_id）
- 返回统一结构的 ToolResult

## 非目标

- 不参与 Agent 推理
- 不处理用户消息
- 不做知识检索
- 不做审批状态管理——审批流程由 web-server 负责

## 技术栈

| 依赖 | 用途 |
|------|------|
| fastmcp | MCP Server 框架 |
| psutil | 系统指标采集（CPU/内存/磁盘/网络/进程） |
| systemd-python | systemd 服务管理（首版：status + restart） |
| tree-sitter | bash 命令 AST 解析（只读免审批分类） |
| tree-sitter-bash | tree-sitter bash grammar |

## 工具注册

工具通过 fastmcp 的 `@server.tool()` 装饰器注册，自定义元数据通过 `meta` 参数声明：

```python
@server.tool(
    name="get_cpu_info",
    description="获取 CPU 使用率、负载和核心温度",
    meta={"is_read_only": True, "is_rollbackable": True, "mutable": False},
)
def get_cpu_info(config: ToolServerConfig = Depends()):
    ...

@server.tool(
    name="bash",
    description="在目标主机上执行 bash 命令",
    meta={"is_read_only": False, "is_rollbackable": False, "mutable": True},
)
def run_bash(command: str, config: ToolServerConfig = Depends()):
    ...
```

### 可变工具与伴生分类工具

可变工具（`meta.mutable = true`）需要附加分类函数 `__classify__`：

```python
def classify_bash(command: str) -> dict:
    """判定 bash 命令是否可作为只读操作免审批。FAIL-CLOSED。"""
    ...

run_bash.__classify__ = classify_bash
```

tool-server 启动时自动扫描已注册工具，对带有 `__classify__` 属性的工具自动生成伴生分类工具：

- 命名：`{tool_name}_classify`（如 `bash_classify`）
- `meta.hidden = true`，不暴露给 LLM
- `meta.mutable = false`，`meta.is_read_only = true`
- `inputSchema` 由 `classify_fn` 的函数签名自动推断（仅关键参数）
- 返回 `{"safe": bool}`。`safe=true` 仅表示“可免审批只读执行”；`safe=false` 表示“需要审批”，不表示 tool-server 强制禁止该命令。

#### bash 分类规则

`bash_classify` 按本项目运维工具定位收窄为“只读免审批”判定：

| 类别 | `safe=true` 例子 | `safe=false` 例子 |
|------|------------------|-------------------|
| 文件与日志读取 | `ls /tmp`、`cat /var/log/syslog`、`sed -n '1,10p' /var/log/syslog` | `sed -i 's/a/b/' file`、`echo hi > file` |
| 系统观测 | `ps aux`、`free -h`、`dmesg -T`、`journalctl -u nginx --no-pager` | `kill 123`、`systemctl restart nginx` |
| 网络观测 | `ss -tulpn`、`ip addr`、`netstat -tulpn` | `curl https://...`、`wget ...`、`ssh host`、`nc host 443` |
| Git 诊断 | `git status --short`、`git log --oneline`、`git diff --stat` | `git reset --hard`、`git clean -fd`、`git push --force` |
| 敏感读取 | — | `cat /etc/shadow`、`cat ~/.ssh/id_rsa`、`cat /proc/1/environ` |
| Shell 执行面 | 普通只读 pipeline：`ps aux \| grep nginx \| wc -l` | `bash -c ...`、`sh -c ...`、`eval ...`、`xargs ...`、命令替换 `$()` |

分类流程：

1. 空命令直接 fail-closed。
2. 使用 `tree-sitter-bash` 解析 AST；解析失败、未知节点、重定向、命令替换、子 shell、循环/条件/函数等复杂结构均为 `safe=false`。
3. 对命令文本做危险模式检测：反引号、`$()`、`${...}`、`$[...]`、进程替换、`$IFS`、重定向符、危险换行等均为 `safe=false`。
4. 将 pipeline / `&&` / `||` / `;` 分段，每段必须命中运维只读命令 allowlist。
5. 对每个命令做 flag 级校验；可写、执行、网络、远程、in-place、output 等 flag 均为 `safe=false`。
6. 拒绝路径形式的命令名（如 `/bin/cat`）、`~user` 展开、以及归一化后命中敏感位置的路径；密钥、凭据、`/proc/*/environ` 等路径均为 `safe=false`。

`sed` 和 `awk` 只允许读取/格式化输出子集。`sed -i`、`sed -f`、`e/r/w/W/R` 命令、带地址表达式的写入/读取/执行绕过，以及 `s///e`、`s///w` 均拒绝；`awk` 中的 `system()`、重定向写入、管道输出等执行或写入形式均拒绝。

注册流程见 `main.py` 的 `_register_classify_companions()`。

## 工具输出控制

LLM 通过 prompt 指示词要求只获取必要信息（使用过滤参数如 `grep`、`since`、`top_n`）。工具层同时设置默认截断安全上限，防止大输出直接注入 LLM 上下文：

| 工具 | 默认上限 | 可调参数 |
|------|----------|----------|
| `read_logs` | 1000 行 | `max_lines`, `grep`, `since` |
| `get_process_list` | 50 个进程 | `top_n` |

LLM 发现输出被截断时，可调整参数重新获取。

## 安全校验

tool-server 收到 tool_call 时执行防御性校验（不查询 web-server 状态机）：

| 校验项 | 规则 |
|--------|------|
| `approval_status` | 必须为 `APPROVED` |
| 只读工具 | `isReadOnly=true` 可不携带 `request_id` |
| 高风险工具 | `isReadOnly=false` 必须携带非空 UUID 格式 `request_id` |

未通过 → 返回 `SECURITY_VIOLATION`（CRITICAL），拒绝执行。

PostgreSQL 只读查询工具额外做 SQL 白名单校验：仅允许单条 `SELECT`/`WITH` 查询，拒绝多语句、事务控制、DDL/DML、`COPY`、`DO`、`CALL`、`NOTIFY`、大对象导入导出、服务端程序执行、`pg_read_file` / `pg_read_binary_file` / `pg_stat_file`、`pg_ls_dir` 和 `pg_ls_*dir` 系列文件系统访问函数。

## 依赖注入

```
class ToolServerConfig:
    proc_path: str                       # 默认 /proc
    log_dir: str                         # 默认 /app/logs
    sandbox_root: str                    # 默认 /app/sandbox，测试临时目录
    cache_ttl: int                       # 指标缓存 TTL 秒数
    bash_timeout: int                    # bash 执行超时秒数
    host_exec: str                       # direct / chroot / nsenter
    host: str                            # 默认 127.0.0.1；Compose 内用 TOOLSERVER_HOST=0.0.0.0
    port: int                            # 默认 11451
    postgres_dsn: str | None             # 未配置时 PostgreSQL 工具返回 FAILED
    postgres_statement_timeout_ms: int
    postgres_max_rows: int
```

生产 Compose 会把 `/app/sandbox` 挂载为 64 MiB tmpfs，避免 bash 工具默认工作目录落到镜像层或持久化卷。
