#!/usr/bin/env python3
from __future__ import annotations

import html
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "ppt-assets" / "slides"
W = 1920
H = 1080


COLORS = {
    "ink": "#162233",
    "muted": "#607385",
    "soft": "#f5f8fb",
    "panel": "#ffffff",
    "line": "#d7e3eb",
    "teal": "#1f8f7a",
    "teal2": "#35c2a4",
    "blue": "#2b6cb0",
    "blue2": "#6aa6ff",
    "amber": "#d58b22",
    "amber2": "#f6c15d",
    "red": "#c94a4a",
    "green": "#1f9d62",
    "dark": "#0e1a2a",
}


def esc(text: str) -> str:
    return html.escape(text, quote=True)


def split_text(text: str, max_chars: int) -> list[str]:
    words = text.split()
    if len(words) > 1 and all(len(w) < max_chars for w in words):
        lines: list[str] = []
        cur = ""
        for word in words:
            trial = f"{cur} {word}".strip()
            if len(trial) <= max_chars:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        return lines
    lines = []
    while text:
        lines.append(text[:max_chars])
        text = text[max_chars:]
    return lines


def text(
    x: int,
    y: int,
    value: str,
    size: int = 28,
    color: str = COLORS["ink"],
    weight: int = 400,
    anchor: str = "start",
) -> str:
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
        f'font-weight="{weight}" text-anchor="{anchor}">{esc(value)}</text>'
    )


def text_block(
    x: int,
    y: int,
    items: list[str],
    size: int = 28,
    color: str = COLORS["ink"],
    max_chars: int = 28,
    line_h: int | None = None,
    bullet: bool = False,
    weight: int = 400,
) -> str:
    line_h = line_h or int(size * 1.55)
    out = []
    cy = y
    for item in items:
        wrapped = split_text(item, max_chars)
        for i, line in enumerate(wrapped):
            prefix = "• " if bullet and i == 0 else "  " if bullet else ""
            out.append(text(x, cy, prefix + line, size, color, weight))
            cy += line_h
        cy += 8
    return "\n".join(out)


def rect(x: int, y: int, w: int, h: int, fill: str, rx: int = 18, stroke: str | None = None, sw: int = 2) -> str:
    stroke_attr = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}"{stroke_attr}/>'


def line(x1: int, y1: int, x2: int, y2: int, color: str = "#7890a4", sw: int = 4, arrow: bool = False) -> str:
    marker = ' marker-end="url(#arrow)"' if arrow else ""
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{sw}" stroke-linecap="round"{marker}/>'


def chip(x: int, y: int, label: str, fill: str, width: int | None = None) -> str:
    width = width or max(108, len(label) * 25 + 42)
    return "\n".join(
        [
            rect(x, y, width, 48, fill, 12),
            text(x + width // 2, y + 32, label, 22, "#ffffff", 700, "middle"),
        ]
    )


def card(x: int, y: int, w: int, h: int, title: str, body: list[str], accent: str = COLORS["teal"]) -> str:
    return "\n".join(
        [
            f'<g filter="url(#shadow)">',
            rect(x, y, w, h, COLORS["panel"], 18, COLORS["line"]),
            rect(x, y, w, 12, accent, 18),
            text(x + 28, y + 58, title, 29, COLORS["ink"], 800),
            text_block(x + 28, y + 102, body, 23, COLORS["muted"], max(16, (w - 70) // 26), 34, True),
            "</g>",
        ]
    )


def shell(title: str, subtitle: str, body: str, page: int, dark: bool = False) -> str:
    if dark:
        bg = """
        <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#07111f"/>
          <stop offset="0.52" stop-color="#0d2433"/>
          <stop offset="1" stop-color="#173323"/>
        </linearGradient>
        """
        base = '<rect width="1920" height="1080" fill="url(#bg)"/>'
        title_color = "#ffffff"
        sub_color = "#c0d0dd"
        footer_color = "#9db4c8"
    else:
        bg = '<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f7fafc"/><stop offset="1" stop-color="#edf4f1"/></linearGradient>'
        base = '<rect width="1920" height="1080" fill="url(#bg)"/>'
        title_color = COLORS["ink"]
        sub_color = COLORS["muted"]
        footer_color = COLORS["muted"]
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
  <defs>
    {bg}
    <filter id="shadow" x="-20%" y="-20%" width="140%" height="150%">
      <feDropShadow dx="0" dy="14" stdDeviation="18" flood-color="#142235" flood-opacity="0.16"/>
    </filter>
    <marker id="arrow" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto">
      <path d="M0,0 L12,6 L0,12 Z" fill="#6f8499"/>
    </marker>
    <style>
      svg {{ font-family: "Noto Sans CJK SC", "Microsoft YaHei", Arial, sans-serif; letter-spacing: 0; }}
    </style>
  </defs>
  {base}
  <circle cx="1660" cy="160" r="280" fill="#35c2a4" opacity="0.08"/>
  <circle cx="1460" cy="930" r="250" fill="#2b6cb0" opacity="0.07"/>
  <g>
    {text(110, 96, title, 48, title_color, 850)}
    {text(112, 142, subtitle, 25, sub_color, 400)}
  </g>
  {body}
  <g>
    {text(110, 1024, "Alascup Agent · 安全智能运维 Agent 软件功能演示", 20, footer_color)}
    {text(1810, 1024, f"{page:02d}/22", 20, footer_color, 700, "end")}
  </g>
</svg>'''


def horizontal_flow(x: int, y: int, labels: list[str], colors: list[str] | None = None, w: int = 250) -> str:
    colors = colors or [COLORS["teal"], COLORS["blue"], COLORS["amber"], COLORS["green"]]
    out = []
    for i, label in enumerate(labels):
        cx = x + i * (w + 78)
        out.append(rect(cx, y, w, 92, "#ffffff", 16, COLORS["line"]))
        out.append(rect(cx, y, w, 10, colors[i % len(colors)], 16))
        out.append(text(cx + w // 2, y + 56, label, 25, COLORS["ink"], 750, "middle"))
        if i < len(labels) - 1:
            out.append(line(cx + w + 18, y + 46, cx + w + 58, y + 46, arrow=True))
    return "\n".join(out)


def slide01() -> str:
    body = """
      <circle cx="1280" cy="360" r="520" fill="#26d6a6" opacity="0.14"/>
      <circle cx="610" cy="640" r="470" fill="#3c8cff" opacity="0.13"/>
      <g transform="translate(1195 240)" filter="url(#shadow)">
        <path d="M260 0 L520 150 L520 450 L260 600 L0 450 L0 150 Z" fill="#10243a" stroke="#55d6c2" stroke-width="4"/>
        <path d="M260 58 L470 180 L470 420 L260 542 L50 420 L50 180 Z" fill="#0c1728" stroke="#3c8cff" stroke-width="3" opacity="0.9"/>
        <circle cx="260" cy="300" r="112" fill="#123d49" stroke="#6ee7d8" stroke-width="5"/>
        <path d="M210 310 L246 346 L318 250" fill="none" stroke="#82f7d1" stroke-width="18" stroke-linecap="round" stroke-linejoin="round"/>
      </g>
      <g transform="translate(130 212)">
        <text x="0" y="0" font-size="34" fill="#79e6d1">Alascup Agent</text>
        <text x="0" y="100" font-size="72" font-weight="800" fill="#f5fbff">面向银河麒麟操作系统的</text>
        <text x="0" y="194" font-size="86" font-weight="900" fill="#ffffff">安全智能运维 Agent</text>
        <text x="0" y="270" font-size="30" fill="#c0d0dd">基于大模型 + MCP + 安全护栏的可审计 OS 运维智能体</text>
      </g>
    """
    chips = "\n".join(
        chip(130 + i * 190, 600, label, color, 166)
        for i, (label, color) in enumerate(
            [("自然语言运维", "#123d49"), ("MCP 工具编排", "#102a47"), ("安全审批", "#3a2b1b"), ("全链路审计", "#1b3a2d")]
        )
    )
    body += chips + text(130, 925, "中国软件杯软件功能演示 · LoongArch + 麒麟高级服务器版 V11", 28, "#c0d0dd")
    return shell("", "", body, 1, True)


def slide02() -> str:
    body = "\n".join(
        [
            card(120, 230, 520, 520, "传统运维痛点", ["脚本分散，依赖个人经验", "故障证据跨进程、日志、网络和配置", "执行动作风险高，过程难以复盘"], COLORS["red"]),
            card(700, 230, 520, 520, "AI Agent 机会", ["自然语言描述目标", "自动选择工具收集证据", "围绕根因持续迭代分析"], COLORS["teal"]),
            card(1280, 230, 520, 520, "核心挑战", ["大模型推理存在不确定性", "不能把 root 权限直接交给模型", "需要安全护栏与审计闭环"], COLORS["amber"]),
            horizontal_flow(320, 835, ["告警", "诊断", "审批", "执行", "审计回放"], [COLORS["red"], COLORS["blue"], COLORS["amber"], COLORS["green"], COLORS["teal"]], 190),
        ]
    )
    return shell("从人工排障到可信智能运维", "在提升排障效率的同时，解决 AI 推理不可控和 OS 操作高风险问题", body, 2)


def slide03() -> str:
    rows = [
        ("OS 环境深度感知", "CPU、内存、磁盘、网络、进程、日志、systemd 状态"),
        ("MCP 运维插件化", "fastmcp tool-server 暴露工具，web-server 动态发现"),
        ("安全意图校验器", "规则引擎 + 伴生分类工具 + bash AST 风险分类"),
        ("最小权限代理执行", "容器化执行层，挂载范围和 capabilities 控制边界"),
        ("推理链路溯源", "会话、消息、工具调用、审计事件、LLM trace 全落库"),
    ]
    out = [rect(160, 220, 1600, 590, "#ffffff", 22, COLORS["line"])]
    out += [rect(160, 220, 1600, 72, COLORS["dark"], 22), text(210, 266, "赛题要求", 28, "#ffffff", 800), text(790, 266, "项目响应", 28, "#ffffff", 800)]
    y = 316
    for i, (a, b) in enumerate(rows):
        fill = "#f8fbfd" if i % 2 == 0 else "#ffffff"
        out.append(rect(190, y - 42, 1540, 84, fill, 8))
        out.append(text(210, y + 8, a, 27, COLORS["ink"], 800))
        out.append(text(790, y + 8, b, 26, COLORS["muted"]))
        y += 94
    out.append(text(220, 895, "定位：不是“能执行命令的聊天机器人”，而是可治理、可审批、可追溯的 OS 运维 Agent。", 30, COLORS["teal"], 800))
    return shell("围绕五大能力构建完整闭环", "赛题功能要求与 Alascup Agent 实现映射", "\n".join(out), 3)


def slide04() -> str:
    body = "\n".join(
        [
            rect(135, 226, 1650, 625, "#ffffff", 22, COLORS["line"]),
            card(190, 290, 360, 300, "B/S 运维入口", ["浏览器聊天界面", "会话列表与历史回看", "Markdown 安全渲染"], COLORS["blue"]),
            card(590, 290, 360, 300, "对话式诊断", ["自然语言输入目标", "Agent 自动收集证据", "汇总异常与建议"], COLORS["teal"]),
            card(990, 290, 360, 300, "受控执行", ["只读工具自动执行", "高风险动作内联审批", "拒绝或超时自动终止"], COLORS["amber"]),
            card(1390, 290, 340, 300, "审计复盘", ["工具调用全记录", "LLM trace 与 token", "异常行为可追溯"], COLORS["green"]),
            horizontal_flow(330, 710, ["用户目标", "Agent 诊断", "安全审批", "工具执行", "审计落库"], w=200),
        ]
    )
    return shell("面向麒麟服务器节点的 B/S 智能运维平台", "软件功能演示聚焦：能诊断、敢审批、可追溯、易部署", body, 4)


def slide05() -> str:
    body = """
    <g filter="url(#shadow)">
      <rect x="110" y="210" width="1700" height="650" rx="24" fill="#ffffff" stroke="#d7e3eb"/>
      <rect x="150" y="270" width="260" height="470" rx="18" fill="#102033"/>
      <text x="280" y="328" text-anchor="middle" font-size="28" fill="#ffffff" font-weight="800">前端展示层</text>
      <text x="280" y="390" text-anchor="middle" font-size="23" fill="#b6c9da">Vue 3 / Nginx</text>
      <text x="280" y="430" text-anchor="middle" font-size="23" fill="#b6c9da">聊天 / 审批 / 历史</text>

      <rect x="520" y="270" width="330" height="470" rx="18" fill="#f8fbfd" stroke="#d7e3eb"/>
      <rect x="520" y="270" width="330" height="64" rx="18" fill="#1f8f7a"/>
      <text x="685" y="313" text-anchor="middle" font-size="27" fill="#ffffff" font-weight="800">智能编排层</text>
      <text x="560" y="390" font-size="25" fill="#162233" font-weight="800">FastAPI + AgentLoop</text>
      <text x="560" y="438" font-size="23" fill="#607385">ReAct 循环 / SSE</text>
      <text x="560" y="478" font-size="23" fill="#607385">Context Manager</text>
      <text x="560" y="518" font-size="23" fill="#607385">工具审查 / 会话管理</text>

      <rect x="970" y="270" width="330" height="470" rx="18" fill="#fffaf0" stroke="#f0d6a0"/>
      <rect x="970" y="270" width="330" height="64" rx="18" fill="#d58b22"/>
      <text x="1135" y="313" text-anchor="middle" font-size="27" fill="#ffffff" font-weight="800">安全护栏层</text>
      <text x="1010" y="390" font-size="25" fill="#162233" font-weight="800">规则 + 审批 + 熔断</text>
      <text x="1010" y="438" font-size="23" fill="#607385">白名单 / 黑名单</text>
      <text x="1010" y="478" font-size="23" fill="#607385">bash 分类 / fail-closed</text>
      <text x="1010" y="518" font-size="23" fill="#607385">执行前二次校验</text>

      <rect x="1420" y="270" width="330" height="470" rx="18" fill="#f8fbfd" stroke="#d7e3eb"/>
      <rect x="1420" y="270" width="330" height="64" rx="18" fill="#2b6cb0"/>
      <text x="1585" y="313" text-anchor="middle" font-size="27" fill="#ffffff" font-weight="800">MCP 工具层</text>
      <text x="1460" y="390" font-size="25" fill="#162233" font-weight="800">fastmcp tool-server</text>
      <text x="1460" y="438" font-size="23" fill="#607385">CPU / 内存 / 磁盘</text>
      <text x="1460" y="478" font-size="23" fill="#607385">网络 / 进程 / 日志</text>
      <text x="1460" y="518" font-size="23" fill="#607385">bash / systemd</text>
    </g>
    """ + "\n".join(
        [
            line(420, 505, 510, 505, arrow=True),
            line(860, 505, 960, 505, arrow=True),
            line(1310, 505, 1410, 505, arrow=True),
            rect(570, 795, 780, 70, "#ffffff", 16, COLORS["line"]),
            text(620, 839, "PostgreSQL：会话、消息、工具调用、审计事件、LLM trace", 25, COLORS["muted"]),
        ]
    )
    return shell("感知 - 决策 - 护栏三层协同", "从浏览器自然语言入口到麒麟 OS 工具执行的完整链路", body, 5)


def slide06() -> str:
    out = [
        horizontal_flow(210, 330, ["Thought\n理解目标", "Action\n调用工具", "Observation\n读取结果", "Review\n安全审查", "Done\n输出结论"], w=230),
        card(210, 560, 430, 210, "并行只读预执行", ["静态只读工具在 think 结束后并行执行", "减少端到端诊断延迟"], COLORS["teal"]),
        card(745, 560, 430, 210, "循环退出条件", ["LLM 判断完成", "轮次上限或 token 熔断", "审批拒绝或错误退出"], COLORS["blue"]),
        card(1280, 560, 430, 210, "演示脚本", ["服务器响应变慢", "排查 CPU 和内存异常", "汇总高占用进程"], COLORS["amber"]),
    ]
    return shell("让 Agent 像运维专家一样迭代排查", "Thought - Action - Observation 循环把推理、工具和证据串起来", "\n".join(out), 6)


def slide07() -> str:
    labels = [
        ("CPU", "负载 / 核心占用", COLORS["teal"]),
        ("内存", "使用率 / OOM 风险", COLORS["blue"]),
        ("磁盘", "分区 / 目录占用", COLORS["amber"]),
        ("网络", "连接 / 端口状态", COLORS["green"]),
        ("进程", "资源占用 / 僵尸进程", "#805ad5"),
        ("日志", "journalctl / 关键词", "#dd6b20"),
        ("服务", "systemd status / restart", "#3182ce"),
        ("eBPF", "tcpdrop / oomkill / biolatency", "#2c7a7b"),
    ]
    out = []
    x0, y0 = 170, 245
    for i, (a, b, c) in enumerate(labels):
        x = x0 + (i % 4) * 420
        y = y0 + (i // 4) * 220
        out.append(card(x, y, 350, 165, a, [b], c))
    out.append(text(210, 830, "核心价值：不是单点指标采集，而是为 Agent 提供可组合的运维证据上下文。", 31, COLORS["teal"], 800))
    out.append(text(210, 885, "边界说明：eBPF 当前作为预置探针与测试适配能力，不宣称替代完整监控平台。", 25, COLORS["muted"]))
    return shell("从系统指标到运维上下文的多维感知", "覆盖性能、进程、日志、服务状态，并为深度内核观测预留扩展", "\n".join(out), 7)


def slide08() -> str:
    tools = ["get_cpu_info", "get_memory_info", "get_disk_usage", "get_network_info", "get_process_list", "read_logs", "bash / bash_classify", "systemd status / restart"]
    out = [rect(160, 220, 1600, 610, "#ffffff", 24, COLORS["line"]), text(220, 290, "MCP Tool Registry", 34, COLORS["ink"], 850)]
    for i, t in enumerate(tools):
        x = 220 + (i % 4) * 380
        y = 340 + (i // 4) * 160
        out.append(rect(x, y, 310, 92, "#f8fbfd", 14, COLORS["line"]))
        out.append(rect(x, y, 10, 92, [COLORS["teal"], COLORS["blue"], COLORS["amber"], COLORS["green"]][i % 4], 14))
        out.append(text(x + 28, y + 56, t, 24, COLORS["ink"], 800))
    out += [
        text(220, 720, "统一元数据：is_read_only / is_rollbackable / mutable / hidden", 28, COLORS["teal"], 800),
        text(220, 775, "可变工具通过伴生分类工具进行运行时风险判断，例如 bash → bash_classify。", 25, COLORS["muted"]),
    ]
    return shell("把 OS 运维能力标准化为 Agent 可调用工具", "插件化工具体系让能力边界清晰，新增工具无需改动核心 Agent 主循环", "\n".join(out), 8)


def slide09() -> str:
    out = [
        card(170, 270, 460, 360, "第一层：web-server 审查", ["统一拦截所有 tool_call", "规则匹配与审批流转", "审计事件写入"], COLORS["teal"]),
        card(730, 270, 460, 360, "第二层：tool-server 校验", ["校验 approval_status", "高风险工具要求 request_id", "未授权返回 SECURITY_VIOLATION"], COLORS["amber"]),
        card(1290, 270, 460, 360, "第三层：熔断与恢复", ["最大迭代轮次", "Token 硬上限", "工具超时和审批超时"], COLORS["blue"]),
        horizontal_flow(340, 760, ["只读自动执行", "高风险需审批", "黑名单拒绝", "审计留痕"], [COLORS["green"], COLORS["amber"], COLORS["red"], COLORS["teal"]], 260),
    ]
    return shell("三层安全护栏，解决 AI 推理不可控", "所有工具调用都经过审查、授权和记录，避免模型绕过执行边界", "\n".join(out), 9)


def slide10() -> str:
    rows = [
        ("ps aux | grep nginx", "只读", "自动执行"),
        ("journalctl -u nginx --no-pager", "只读", "自动执行"),
        ("rm -rf /var/log/app.log", "高风险", "需要审批"),
        ("cat /etc/shadow", "敏感读取", "fail-closed"),
        ("bash -c '...'", "复杂执行面", "fail-closed"),
    ]
    out = [rect(180, 245, 1560, 520, "#ffffff", 22, COLORS["line"]), rect(180, 245, 1560, 70, COLORS["dark"], 22)]
    for x, h in [(230, "命令"), (920, "分类"), (1250, "行为")]:
        out.append(text(x, 292, h, 26, "#ffffff", 800))
    y = 365
    for i, row in enumerate(rows):
        out.append(rect(220, y - 42, 1480, 70, "#f8fbfd" if i % 2 == 0 else "#ffffff", 8))
        out.append(text(240, y + 4, row[0], 25, COLORS["ink"], 700))
        out.append(text(940, y + 4, row[1], 25, COLORS["amber"] if i >= 2 else COLORS["teal"], 800))
        out.append(text(1260, y + 4, row[2], 25, COLORS["muted"], 700))
        y += 86
    out.append(text_block(230, 835, ["tree-sitter-bash 解析 AST，解析失败、重定向、子 shell、命令替换、敏感路径均按 fail-closed 处理。"], 27, COLORS["muted"], 58))
    return shell("对命令行执行面进行细粒度收敛", "bash 是最灵活也最危险的入口，因此按只读免审批能力严格分类", "\n".join(out), 10)


def slide11() -> str:
    out = [
        horizontal_flow(180, 320, ["Agent 生成工具请求", "审查层判定高风险", "用户内联审批", "tool-server 执行"], [COLORS["blue"], COLORS["amber"], COLORS["teal"], COLORS["green"]], 300),
        card(205, 575, 440, 240, "审批卡片", ["展示工具名、参数、原因", "支持 Approve / Reject", "默认 5 分钟超时拒绝"], COLORS["amber"]),
        card(740, 575, 440, 240, "最小权限执行", ["tool-server 独立容器边界", "web-server 不直接操作宿主机", "挂载范围与 capabilities 约束"], COLORS["teal"]),
        card(1275, 575, 440, 240, "执行校验", ["必须携带 APPROVED", "高风险请求必须有 request_id", "违规直接拒绝并记录"], COLORS["blue"]),
    ]
    return shell("让关键操作可控地自动化", "非只读动作必须经过人工确认，未授权不执行", "\n".join(out), 11)


def slide12() -> str:
    out = [
        horizontal_flow(160, 315, ["接收指令", "感知环境", "推理决策", "安全校验", "执行结果", "审计事件"], [COLORS["blue"], COLORS["teal"], COLORS["amber"], COLORS["red"], COLORS["green"], COLORS["teal"]], 190),
        card(180, 545, 350, 220, "messages", ["用户消息", "Agent 回复", "reasoning_content", "tool_result"], COLORS["blue"]),
        card(570, 545, 350, 220, "tool_calls", ["参数", "审批状态", "执行状态", "结果和错误"], COLORS["teal"]),
        card(960, 545, 350, 220, "audit_events", ["仅追加写入", "actor / event", "decision / transition"], COLORS["amber"]),
        card(1350, 545, 350, 220, "llm_traces", ["模型与 token", "延迟", "prompt / completion"], COLORS["green"]),
        text(220, 870, "评委能看到的不只是结果，还能复盘：为什么这样做、谁批准了、执行结果是什么。", 30, COLORS["teal"], 800),
    ]
    return shell("从用户指令到执行结果全程留痕", "PostgreSQL 持久化会话、消息、工具调用、审计事件和 LLM trace", "\n".join(out), 12)


def slide13() -> str:
    # Compact UI mockup tailored for a slide.
    body = """
    <g filter="url(#shadow)">
      <rect x="145" y="210" width="1630" height="660" rx="24" fill="#ffffff"/>
      <rect x="145" y="210" width="1630" height="64" rx="24" fill="#0e1a2a"/>
      <circle cx="182" cy="242" r="10" fill="#ff6b6b"/><circle cx="214" cy="242" r="10" fill="#ffd166"/><circle cx="246" cy="242" r="10" fill="#4dd599"/>
      <text x="290" y="252" font-size="24" fill="#ffffff">Alascup Agent 控制台</text>
      <rect x="145" y="274" width="320" height="596" fill="#101927"/>
      <text x="190" y="345" font-size="30" fill="#dce8f3" font-weight="800">会话</text>
      <rect x="190" y="384" width="230" height="52" rx="12" fill="#1f8f7a"/><text x="222" y="418" font-size="22" fill="#ffffff">+ New Session</text>
      <rect x="185" y="490" width="250" height="64" rx="12" fill="#1a2a3d"/><text x="210" y="530" font-size="21" fill="#e8f1fa">磁盘空间异常排查</text>
      <rect x="185" y="570" width="250" height="64" rx="12" fill="#132133"/><text x="210" y="610" font-size="21" fill="#a8b9ca">服务状态检查</text>
      <text x="520" y="340" font-size="32" fill="#162233" font-weight="800">磁盘空间异常排查</text>
      <rect x="1090" y="380" width="570" height="70" rx="18" fill="#e8f2ff"/><text x="1128" y="424" font-size="25" fill="#25313d">磁盘快满了，请给出清理建议。</text>
      <rect x="560" y="485" width="750" height="78" rx="18" fill="#ffffff" stroke="#d7e3eb"/><text x="590" y="533" font-size="25" fill="#1f8f7a" font-weight="800">Reasoning</text><text x="730" y="533" font-size="25" fill="#25313d">先收集证据，再判断风险。</text>
      <rect x="560" y="605" width="475" height="92" rx="18" fill="#ffffff" stroke="#d7e3eb"/><text x="600" y="662" font-size="27" fill="#162233" font-weight="800">R  get_disk_usage</text><rect x="890" y="628" width="90" height="42" rx="10" fill="#dff7eb"/><text x="913" y="656" font-size="21" fill="#16794f">Done</text>
      <rect x="1085" y="605" width="475" height="92" rx="18" fill="#ffffff" stroke="#d7e3eb"/><text x="1125" y="662" font-size="27" fill="#162233" font-weight="800">R  read_logs</text><rect x="1415" y="628" width="90" height="42" rx="10" fill="#dff7eb"/><text x="1438" y="656" font-size="21" fill="#16794f">Done</text>
      <rect x="560" y="735" width="1000" height="96" rx="18" fill="#fff8ea" stroke="#f0c56b"/><text x="600" y="794" font-size="27" fill="#162233" font-weight="800">W  需要审批：清理 /var/log/app/*.old</text><rect x="1310" y="760" width="120" height="44" rx="10" fill="#1f8f7a"/><text x="1342" y="789" font-size="21" fill="#ffffff">Approve</text>
    </g>
    """
    return shell("面向运维人员的低门槛对话式工作台", "同一界面展示推理、工具调用、审批、错误提示和历史会话", body, 13)


def slide14() -> str:
    out = [
        card(160, 255, 360, 240, "目标环境", ["LoongArch 架构", "麒麟高级服务器版 V11", "Linux 6.6 内核背景"], COLORS["blue"]),
        card(565, 255, 360, 240, "容器化部署", ["Docker Compose", "frontend / web-server", "tool-server / PostgreSQL"], COLORS["teal"]),
        card(970, 255, 360, 240, "网络边界", ["frontend 暴露 80", "web-server 经 nginx 代理", "tool-server 仅内部访问"], COLORS["amber"]),
        card(1375, 255, 360, 240, "集中配置", ["servers.json", "rules.json", "llm.json", "logs 挂载与审计落库"], COLORS["green"]),
        horizontal_flow(255, 710, ["浏览器", "Nginx 前端", "web-server", "tool-server", "麒麟 OS"], w=230),
        text(210, 870, "边界：当前完成应用层 Agent、容器化隔离和工具安全控制，不宣称已深度集成 KySEC/KSAF。", 25, COLORS["muted"]),
    ]
    return shell("面向国产软硬件环境的部署设计", "面向 LoongArch + 麒麟 V11 的微服务部署与适配方案", "\n".join(out), 14)


def demo_slide(page: int, title: str, subtitle: str, prompt: str, steps: list[str], values: list[str], accent: str) -> str:
    out = [
        rect(150, 230, 1620, 600, "#ffffff", 24, COLORS["line"]),
        text(210, 300, "演示输入", 30, COLORS["ink"], 850),
        rect(210, 330, 1180, 76, "#e8f2ff", 18),
        text(245, 379, prompt, 26, COLORS["ink"], 700),
        text(210, 485, "演示流程", 30, COLORS["ink"], 850),
    ]
    for i, s in enumerate(steps):
        y = 535 + i * 58
        out.append(chip(220, y - 34, str(i + 1), accent, 58))
        out.append(text(300, y, s, 25, COLORS["muted"]))
    out.append(card(1220, 470, 460, 260, "展示价值", values, accent))
    return shell(title, subtitle, "\n".join(out), page)


def slide15() -> str:
    return demo_slide(
        15,
        "自然语言完成 CPU / 内存 / 磁盘 / 网络排查",
        "典型演示一：系统资源健康诊断",
        "查看当前系统 CPU、内存、磁盘和网络状态，指出最值得关注的异常。",
        ["创建新会话", "Agent 形成检查计划", "自动调用多个只读工具", "SSE 流式展示工具结果", "汇总异常点与建议"],
        ["OS 感知能力", "MCP 工具调用", "只读免审批", "自然语言解释"],
        COLORS["teal"],
    )


def slide16() -> str:
    return demo_slide(
        16,
        "清理不是直接删除，而是诊断、评估、审批、执行",
        "典型演示二：磁盘清理的安全闭环",
        "磁盘空间快满了，请分析哪些目录占用较多，并给出安全清理建议。",
        ["调用磁盘和日志只读工具", "定位可疑目录或大日志", "生成清理计划并说明风险", "删除类动作弹出审批卡片", "批准后执行，拒绝后停止高危动作"],
        ["对应赛题场景", "展示安全护栏", "审批与审计闭环", "先证据后行动"],
        COLORS["amber"],
    )


def slide17() -> str:
    return demo_slide(
        17,
        "从服务状态到日志证据的根因分析",
        "典型演示三：服务异常与日志分析",
        "检查 nginx 服务状态和最近 30 分钟日志，判断是否有异常重启或错误。",
        ["查询 systemd 服务状态", "读取限定时间范围日志", "识别错误关键词与异常重启", "涉及 restart 时进入审批", "输出根因、影响范围和建议"],
        ["日志分析", "服务状态诊断", "根因分析", "先诊断后修复"],
        COLORS["blue"],
    )


def slide18() -> str:
    out = [
        card(160, 250, 360, 250, "单元测试", ["web-server", "tool-server", "frontend 关键模块"], COLORS["blue"]),
        card(560, 250, 360, 250, "集成测试", ["MCP client/server", "API / 数据库 / SSE"], COLORS["teal"]),
        card(960, 250, 360, 250, "Mock E2E", ["前端交互", "审批 UI", "错误恢复"], COLORS["amber"]),
        card(1360, 250, 360, 250, "Live E2E", ["nginx + web-server", "tool-server + PostgreSQL", "真实链路可用"], COLORS["green"]),
        rect(240, 670, 1440, 120, "#ffffff", 18, COLORS["line"]),
        text(290, 718, "重点保护路径", 29, COLORS["ink"], 850),
        text(540, 718, "只读自动执行 · 高风险审批 · 非法 request_id 拒绝 · SSE 事件顺序 · 工具分类", 25, COLORS["muted"]),
        text(290, 765, "执行命令", 29, COLORS["ink"], 850),
        text(540, 765, "make test-unit / make test-integration / make test-e2e / make up-test", 25, COLORS["teal"], 800),
    ]
    return shell("围绕安全路径建立可回归测试体系", "测试目标不是追数字，而是保护比赛演示最关键的行为", "\n".join(out), 18)


def slide19() -> str:
    items = [
        ("MCP 插件化工具总线", "运维能力标准化接入，Agent 能力可扩展"),
        ("ReAct + SSE 流式编排", "推理、工具、审批和结果持续呈现"),
        ("双层工具安全判断", "策略审查 + 伴生分类 + 执行前校验"),
        ("bash AST fail-closed", "结构化收敛最危险的通用命令入口"),
        ("审计不可变与 LLM trace", "智能决策过程可回看、可定位、可问责"),
        ("面向麒麟/LoongArch 部署", "适配国产软硬件比赛环境"),
    ]
    out = []
    for i, (a, b) in enumerate(items):
        x = 165 + (i % 3) * 540
        y = 245 + (i // 3) * 260
        out.append(card(x, y, 470, 190, a, [b], [COLORS["teal"], COLORS["blue"], COLORS["amber"], COLORS["green"], "#805ad5", "#dd6b20"][i]))
    return shell("把会调用工具的 Agent 升级为可信运维智能体", "创新点围绕可扩展、可控、可追溯三条主线展开", "\n".join(out), 19)


def slide20() -> str:
    out = [
        card(175, 260, 420, 260, "降低门槛", ["自然语言描述目标即可启动排查", "减少对脚本和经验的强依赖"], COLORS["blue"]),
        card(750, 260, 420, 260, "提升效率", ["Agent 自动选择工具并汇总证据", "减少跨命令手工切换成本"], COLORS["teal"]),
        card(1325, 260, 420, 260, "降低风险", ["高风险默认审批", "未授权动作不执行"], COLORS["amber"]),
        rect(250, 690, 1420, 110, "#ffffff", 18, COLORS["line"]),
        text(300, 735, "适用场景", 30, COLORS["ink"], 850),
        text(520, 735, "教育、医疗、政企数据中心等合规要求较高的 Linux 运维环境", 27, COLORS["muted"]),
        text(300, 780, "扩展方向", 30, COLORS["ink"], 850),
        text(520, 780, "更多 MCP 工具、eBPF 实时观测、麒麟安全策略联动、故障知识库沉淀", 27, COLORS["muted"]),
    ]
    return shell("可落地的安全智能运维底座", "把大模型能力放进可治理的操作系统运维流程中", "\n".join(out), 20)


def slide21() -> str:
    out = [
        card(160, 250, 720, 500, "当前已实现或已设计支撑", ["B/S 聊天式运维界面", "ReAct Agent 循环", "MCP 工具发现与调用", "OS 感知工具与 bash/systemd 运维工具", "高风险审批、安全规则、二次校验", "会话、审计、LLM trace 持久化"], COLORS["teal"]),
        card(1040, 250, 720, 500, "下一步演进", ["故障知识库沉淀", "KySEC/KSAF 策略联动", "eBPF 长时运行和异常订阅", "更精细 RBAC 与组织权限", "诊断延迟和审批耗时性能基准"], COLORS["blue"]),
        text(220, 855, "边界：多租户、统一身份认证、全量实时监控平台是当前非目标，不在演示中夸大。", 26, COLORS["muted"]),
    ]
    return shell("当前已实现能力与下一步演进", "清晰说明项目边界，让能力表达更可信", "\n".join(out), 21)


def slide22() -> str:
    body = """
      <circle cx="1430" cy="420" r="360" fill="#26d6a6" opacity="0.16"/>
      <circle cx="580" cy="680" r="430" fill="#3c8cff" opacity="0.12"/>
      <g transform="translate(150 260)">
        <text x="0" y="0" font-size="70" font-weight="900" fill="#ffffff">让 AI Agent 在操作系统运维中</text>
        <text x="0" y="94" font-size="88" font-weight="900" fill="#82f7d1">可用、可控、可信</text>
        <text x="0" y="178" font-size="31" fill="#c0d0dd">MCP 插件化 · ReAct 智能编排 · 安全审批 · 最小权限执行 · 全链路审计</text>
      </g>
    """ + "\n".join(
        [
            chip(200, 610, "能诊断", "#123d49", 170),
            chip(420, 610, "敢审批", "#3a2b1b", 170),
            chip(640, 610, "可追溯", "#102a47", 170),
            chip(860, 610, "易部署", "#1b3a2d", 170),
            text(150, 900, "Alascup Agent 实现面向麒麟操作系统的安全智能运维闭环。", 32, "#c0d0dd"),
        ]
    )
    return shell("", "", body, 22, True)


SLIDES = [
    slide01,
    slide02,
    slide03,
    slide04,
    slide05,
    slide06,
    slide07,
    slide08,
    slide09,
    slide10,
    slide11,
    slide12,
    slide13,
    slide14,
    slide15,
    slide16,
    slide17,
    slide18,
    slide19,
    slide20,
    slide21,
    slide22,
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    converter = shutil.which("rsvg-convert")
    for idx, fn in enumerate(SLIDES, 1):
        svg = fn()
        svg_path = OUT / f"slide-{idx:02d}.svg"
        png_path = OUT / f"slide-{idx:02d}.png"
        svg_path.write_text(svg, encoding="utf-8")
        if converter:
            subprocess.run([converter, "-w", str(W), "-h", str(H), str(svg_path), "-o", str(png_path)], check=True)
    print(f"Generated {len(SLIDES)} SVG slides in {OUT}")
    if converter:
        print(f"Generated {len(SLIDES)} PNG slides in {OUT}")
    else:
        print("rsvg-convert not found; SVG files were generated, PNG export skipped")


if __name__ == "__main__":
    main()
