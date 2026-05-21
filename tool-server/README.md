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
| tree-sitter | bash 命令 AST 解析（安全分级） |
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
def classify_bash(command: str) -> bool:
    """使用 tree-sitter 解析命令 AST 判定安全/危险。FAIL-CLOSED。"""
    ...

run_bash.__classify__ = classify_bash
```

tool-server 启动时自动扫描已注册工具，对带有 `__classify__` 属性的工具自动生成伴生分类工具：

- 命名：`{tool_name}_classify`（如 `bash_classify`）
- `meta.hidden = true`，不暴露给 LLM
- `meta.mutable = false`，`meta.is_read_only = true`
- `inputSchema` 由 `classify_fn` 的函数签名自动推断（仅关键参数）
- 返回 `{"safe": bool}`

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
| `request_id` | 非空，符合 UUID 格式 |
| 高风险工具 | `isReadOnly=false` 必须携带 `request_id` |

未通过 → 返回 `SECURITY_VIOLATION`（CRITICAL），拒绝执行。

## 依赖注入

```
class ToolServerConfig:
    proc_path: str       # 注入；生产 /proc，测试 /tmp/test-proc
    sys_path: str        # 注入；生产 /sys，测试 /tmp/test-sys
    log_path: str        # 注入；生产 /var/log，测试 /tmp/test-log
    sandbox_root: str    # 注入；生产独立路径，测试临时目录
```
