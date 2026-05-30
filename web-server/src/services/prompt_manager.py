from pathlib import Path

DEFAULT_IDENTITY = "作为运维 Agent，利用可用工具协助运维人员执行任务。任务完成后返回一份精简的报告,仅包括问题根因和解决方法"

DEFAULT_RULES = "你必须遵守以下规则：\n- 禁止执行恶意破坏系统的操作\n- 禁止画蛇添足\n- 禁止半途而废\n- 禁止没有事实依据就给出回答"

DEFAULT_TOOL_USAGE = (
    "工具调用规范：\n"
    "- 先收集信息再行动——优先使用只读工具了解系统状态\n"
    "- 并行调用独立的只读工具以加速信息收集\n"
    "- 验证每个工具的执行结果，失败时分析原因并调整策略\n"
    "- 包含不可回溯或可能对系统安全造成风险的操作必须先输出计划请求用户确认"
)

DEFAULT_MEMORY = ""

SECTION_DEFAULTS = {
    "identity": DEFAULT_IDENTITY,
    "rules": DEFAULT_RULES,
    "tool_usage": DEFAULT_TOOL_USAGE,
}

SECTION_ORDER = ["identity", "rules", "tool_usage", "environment", "memory"]


class PromptManager:
    def __init__(self, prompt_path: Path | None = None) -> None:
        self._prompt_path = prompt_path
        self._overrides: dict[str, str] = {}
        self._cache: dict[str, str] = {}
        self._environment: dict[str, str] = {}
        self._memory: str = ""
        self._load_overrides()

    def _load_overrides(self) -> None:
        if not self._prompt_path or not self._prompt_path.is_file():
            return
        text = self._prompt_path.read_text()
        current_section = ""
        current_content: list[str] = []
        for line in text.split("\n"):
            if line.startswith("## "):
                if current_section and current_content:
                    self._overrides[current_section.strip()] = "\n".join(
                        current_content
                    ).strip()
                current_section = line[3:].strip()
                current_content = []
            else:
                current_content.append(line)
        if current_section and current_content:
            self._overrides[current_section.strip()] = "\n".join(
                current_content
            ).strip()

    def set_environment(self, env: dict[str, str]) -> None:
        self._environment = env

    def set_memory(self, memory: str) -> None:
        self._memory = memory

    def reset_cache(self) -> None:
        self._cache.clear()

    def _get_section(self, name: str) -> str:
        if name in self._overrides:
            return self._overrides[name]
        if name == "environment":
            return self._build_environment()
        if name == "memory":
            return self._memory if self._memory else DEFAULT_MEMORY
        if name in self._cache:
            return self._cache[name]
        default = SECTION_DEFAULTS.get(name, "")
        if name in SECTION_DEFAULTS:
            self._cache[name] = default
        return default

    def _build_environment(self) -> str:
        if not self._environment:
            return "目标系统环境信息暂不可用。"
        lines = ["目标系统环境："]
        for key, val in self._environment.items():
            lines.append(f"- {key}: {val}")
        return "\n".join(lines)

    def build_system_prompt(self) -> str:
        sections = [self._get_section(name) for name in SECTION_ORDER]
        return "\n\n".join(s.strip() for s in sections if s.strip())
