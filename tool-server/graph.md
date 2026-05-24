# tool-server 流程图

## 总览：工具请求处理全链路

```mermaid
flowchart TD
    %% ============================================================
    %% 1. 声明
    %% ============================================================

    %% ── 入口 ──
    mcp_entry["MCP JSON-RPC 请求<br/>web-server → tool-server:11451"]

    %% ── 安全校验 ──
    sec_approval{"二次安全校验<br/>approval_status == APPROVED<br/>且 request_id 有效？"}
    sec_violation["返回 SECURITY_VIOLATION<br/>拒绝执行"]

    %% ── 工具分发 ──
    tool_router{"工具类型？"}
    tool_perception["感知工具<br/>只读系统查询"]
    tool_operation["操作工具<br/>bash / systemd"]

    %% ── 感知工具 ──
    perception_cpu["get_cpu_info<br/>psutil.cpu_percent()"]
    perception_mem["get_memory_info<br/>psutil.virtual_memory()"]
    perception_disk["get_disk_usage<br/>psutil.disk_usage()"]
    perception_net["get_network_info<br/>psutil.net_if_stats()"]
    perception_proc["get_process_list<br/>psutil.process_iter()"]
    perception_log["read_logs<br/>读取 /var/log"]

    %% ── 操作工具 ──
    op_bash["bash 命令执行<br/>沙箱目录 + timeout"]
    op_systemd["systemd 服务管理<br/>start / stop / restart / status"]

    %% ── 执行后处理 ──
    cache_check{"缓存命中？<br/>同参数 + 未过期"}
    cache_store["写入缓存<br/>TTL 可配置"]
    cache_return["返回缓存结果"]
    result_build["构造 ToolResult<br/>execution_status + output"]
    result_return["返回 MCP JSON-RPC 响应"]

    %% ── 错误处理 ──
    error_timeout["执行超时<br/>返回 FAILED"]
    error_exec["执行失败<br/>返回 FAILED + error"]

    %% ============================================================
    %% 2. 连线
    %% ============================================================

    mcp_entry --> sec_approval
    sec_approval -- "否" --> sec_violation
    sec_approval -- "是" --> cache_check

    cache_check -- "命中" --> cache_return
    cache_return --> result_return

    cache_check -- "未命中" --> tool_router
    tool_router -- "感知" --> tool_perception
    tool_router -- "操作" --> tool_operation

    tool_perception --> perception_cpu
    tool_perception --> perception_mem
    tool_perception --> perception_disk
    tool_perception --> perception_net
    tool_perception --> perception_proc
    tool_perception --> perception_log

    tool_operation --> op_bash
    tool_operation --> op_systemd

    perception_cpu --> result_build
    perception_mem --> result_build
    perception_disk --> result_build
    perception_net --> result_build
    perception_proc --> result_build
    perception_log --> result_build
    op_bash --> result_build
    op_systemd --> result_build

    op_bash -.-> error_timeout
    op_systemd -.-> error_timeout
    error_timeout --> result_build
    op_bash -.-> error_exec
    op_systemd -.-> error_exec
    error_exec --> result_build

    result_build --> cache_store
    cache_store --> result_return
```

---

## 二次安全校验（防御性校验）

tool-server 不查询 web-server 的状态机，仅做本地格式与字段校验。

```mermaid
flowchart TD
    %% 声明
    receive["收到 tool_call<br/>JSON-RPC params"]
    check_approval{"approval_status<br/>== APPROVED？"}
    check_uuid{"request_id<br/>非空且符合 UUID 格式？"}
    check_highrisk{"isReadOnly == false<br/>且 request_id 缺失？"}
    pass["校验通过<br/>执行工具"]
    fail["SECURITY_VIOLATION<br/>返回 error code=403"]
    log["web-server 记录 CRITICAL 审计事件"]

    %% 连线
    receive --> check_approval
    check_approval -- "否" --> fail
    check_approval -- "是" --> check_uuid
    check_uuid -- "否" --> fail
    check_uuid -- "是" --> check_highrisk
    check_highrisk -- "是（缺少 request_id）" --> fail
    check_highrisk -- "否" --> pass
    fail --> log
```

> 强校验（request_id 是否对应一个真实的 APPROVED 状态的 ToolRequest）由 web-server 保证。tool-server 仅防御 web-server 绕过审批或代码 bug 导致未审批请求漏过来。

---

## 工具执行状态机

```mermaid
stateDiagram-v2
    [*] --> RECEIVED: 收到 JSON-RPC 请求

    RECEIVED --> SECURITY_CHECK: 提取 approval_status
    SECURITY_CHECK --> REJECTED: 校验失败
    SECURITY_CHECK --> CACHE_LOOKUP: 校验通过

    CACHE_LOOKUP --> CACHE_HIT: 同参数 + 未过期
    CACHE_LOOKUP --> RUNNING: 缓存未命中

    CACHE_HIT --> [*]: 返回缓存结果

    RUNNING --> SUCCEEDED: 执行完成
    RUNNING --> FAILED: 执行错误
    RUNNING --> TIMEOUT: 超时

    SUCCEEDED --> CACHE_WRITE: 写入缓存
    CACHE_WRITE --> [*]: 返回 ToolResult

    FAILED --> [*]: 返回 ToolResult(error)
    TIMEOUT --> [*]: 返回 ToolResult(timeout)
    REJECTED --> [*]: 返回 SECURITY_VIOLATION
```

---

## 感知工具数据流

```mermaid
flowchart LR
    %% 声明
    subgraph host["宿主机（只读挂载）"]
        proc["/proc<br/>CPU / 内存 / 进程"]
        sys["/sys<br/>磁盘 / 网络"]
        varlog["/var/log<br/>系统日志"]
    end

    subgraph tool_server["tool-server 容器"]
        subgraph perception["感知工具集"]
            cpu["get_cpu_info"]
            mem["get_memory_info"]
            disk["get_disk_usage"]
            net["get_network_info"]
            proc_tool["get_process_list"]
            log_tool["read_logs"]
        end
        struct["结构化转换<br/>原始数据 → ToolResult"]
    end

    subgraph output["输出"]
        result["ToolResult<br/>{execution_status, output}"]
    end

    %% 连线
    proc --> cpu
    proc --> mem
    proc --> proc_tool
    sys --> disk
    sys --> net
    varlog --> log_tool

    cpu --> struct
    mem --> struct
    disk --> struct
    net --> struct
    proc_tool --> struct
    log_tool --> struct
    struct --> result
```

---

## 操作工具执行流程（bash）

```mermaid
flowchart TD
    %% 声明
    bash_call["tool_call: bash<br/>params: {command, timeout}"]
    sandbox["切换工作目录到 sandbox_root"]
    timeout["设置超时<br/>默认 30s"]
    exec["subprocess.run(command)<br/>capture stdout/stderr"]
    check_rc{"returncode == 0？"}
    success["SUCCEEDED<br/>output: {stdout, stderr}"]
    fail["FAILED<br/>error: {returncode, stderr}"]
    timeout_kill["超时 → SIGTERM → SIGKILL<br/>返回 TIMEOUT"]

    %% 连线
    bash_call --> sandbox
    sandbox --> timeout
    timeout --> exec
    exec --> check_rc
    check_rc -- "是" --> success
    check_rc -- "否" --> fail
    exec -.-> timeout_kill
```

---

## 模块依赖图

```mermaid
flowchart TD
    %% 声明
    main["main.py<br/>fastmcp Server 入口"]

    subgraph tools["tools/"]
        perception["perception/<br/>cpu, memory, disk<br/>network, process, log_reader"]
        operation["operation/<br/>bash, systemd"]
    end

    subgraph infra["基础设施"]
        security["security/<br/>二次校验"]
        cache["cache/<br/>结果缓存"]
        error_mod["error/<br/>错误定义与格式化"]
    end

    subgraph external["外部依赖"]
        fastmcp["fastmcp<br/>MCP Server 框架"]
        psutil["psutil<br/>系统指标"]
        systemd["systemd-python<br/>D-Bus 通信"]
        host["宿主机文件系统<br/>只读挂载"]
    end

    subgraph config["配置注入"]
        cfg["ToolServerConfig<br/>proc_path / sys_path<br/>log_path / sandbox_root"]
    end

    %% 连线
    main --> fastmcp
    main --> cfg
    fastmcp --> tools
    tools --> security
    tools --> cache
    tools --> error_mod
    perception --> psutil
    perception --> host
    operation --> host
    operation --> systemd
    operation --> error_mod
```

---

## 依赖注入与测试替换

```mermaid
flowchart LR
    %% 声明
    subgraph prod["生产环境"]
        p_proc["/proc"]
        p_sys["/sys"]
        p_log["/var/log"]
        p_sandbox["独立沙箱路径"]
    end

    subgraph test["测试环境"]
        t_proc["/tmp/test-proc"]
        t_sys["/tmp/test-sys"]
        t_log["/tmp/test-log"]
        t_sandbox["临时目录 tmpfs"]
    end

    subgraph config["ToolServerConfig 注入"]
        cfg_proc["proc_path"]
        cfg_sys["sys_path"]
        cfg_log["log_path"]
        cfg_sandbox["sandbox_root"]
    end

    %% 连线
    p_proc -.-> cfg_proc
    p_sys -.-> cfg_sys
    p_log -.-> cfg_log
    p_sandbox -.-> cfg_sandbox
    t_proc -.-> cfg_proc
    t_sys -.-> cfg_sys
    t_log -.-> cfg_log
    t_sandbox -.-> cfg_sandbox
```
