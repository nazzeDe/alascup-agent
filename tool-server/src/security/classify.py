import re

_READONLY_COMMANDS = {"ls", "cat", "head", "tail", "grep", "find", "wc", "sort",
    "uniq", "cut", "tr", "awk", "sed -n", "du", "df", "ps", "top -b",
    "free", "uptime", "who", "w", "last", "dmesg", "journalctl",
    "systemctl status", "systemctl list", "systemctl show"}

_DESTRUCTIVE_PATTERNS = [
    r"\brm\b", r">\s*/", r"mkfs", r"dd\s+if=", r"fdisk", r"parted",
    r"mv\s+.*/etc/", r">\s*/etc/", r"chmod\s+777", r"chown\s+root",
    r"kill\s+-9", r"reboot", r"shutdown", r"halt", r"poweroff",
    r"systemctl\s+(stop|restart|disable|mask)", r"iptables", r"nft\s+add",
]

_READONLY_TOOLS = {
    "get_cpu_info", "get_memory_info", "get_disk_usage",
    "get_process_list", "get_network_info", "read_logs",
    "search_experience",
}

_ROLLBACKABLE_TOOLS = {"delete_temp_files"}


def classify_tool(tool_name: str, params: dict) -> dict:
    is_read_only = _classify(tool_name, params)
    is_rollbackable = tool_name in _ROLLBACKABLE_TOOLS
    return {"isReadOnly": is_read_only, "isRollbackable": is_read_only or is_rollbackable}


def _classify(tool_name: str, params: dict) -> bool:
    if tool_name in _READONLY_TOOLS:
        return True
    if tool_name == "bash":
        return _classify_bash(params.get("command", ""))
    if tool_name == "manage_service":
        action = params.get("action", "")
        return action in ("status", "list", "show", "is-active", "is-enabled")
    return False


def _classify_bash(command: str) -> bool:
    for pat in _DESTRUCTIVE_PATTERNS:
        if re.search(pat, command):
            return False
    return True
