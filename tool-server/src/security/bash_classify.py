"""Bash read-only classifier.

The classifier answers one narrow question: can this command skip approval as
read-only?  ``safe=False`` means "needs approval", not "must never execute".

The design parses with tree-sitter, fails closed on unknown syntax, rejects
shell features that create execution/write/exfiltration surfaces, then allows
only a small operations-focused command/flag/path set.
"""

from __future__ import annotations

import posixpath
import re
import shlex
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from tree_sitter import Parser

FlagArgType = Literal["none", "number", "string", "char"]


@dataclass(frozen=True)
class CommandConfig:
    safe_flags: dict[str, FlagArgType]
    allow_positionals: bool = True
    respects_double_dash: bool = True


@dataclass
class _ShellQuoteState:
    in_single: bool = False
    in_double: bool = False
    escaped: bool = False

    @property
    def is_quoted(self) -> bool:
        return self.in_single or self.in_double

    def consume(self, char: str) -> str:
        if self.escaped:
            self.escaped = False
            return "text"
        if char == "\\" and not self.in_single:
            self.escaped = True
            return "escape"
        if char == "'" and not self.in_double:
            self.in_single = not self.in_single
            return "quote"
        if char == '"' and not self.in_single:
            self.in_double = not self.in_double
            return "quote"
        return "text"


_SAFE_FLAG_GROUPS: dict[str, dict[str, FlagArgType]] = {
    "help": {"--help": "none", "-h": "none", "--version": "none", "-V": "none"},
    "grep": {
        "-e": "string",
        "--regexp": "string",
        "-i": "none",
        "--ignore-case": "none",
        "-F": "none",
        "--fixed-strings": "none",
        "-w": "none",
        "--word-regexp": "none",
        "-v": "none",
        "--invert-match": "none",
        "-c": "none",
        "--count": "none",
        "-l": "none",
        "--files-with-matches": "none",
        "-n": "none",
        "--line-number": "none",
        "-A": "number",
        "--after-context": "number",
        "-B": "number",
        "--before-context": "number",
        "-C": "number",
        "--context": "number",
        "-H": "none",
        "-q": "none",
        "--quiet": "none",
    },
}

_GENERIC_VIEW_FLAGS: dict[str, FlagArgType] = {
    **_SAFE_FLAG_GROUPS["help"],
    "-a": "none",
    "-A": "none",
    "-b": "none",
    "-c": "none",
    "-d": "none",
    "-f": "none",
    "-g": "none",
    "-h": "none",
    "-i": "none",
    "-k": "none",
    "-l": "none",
    "-m": "none",
    "-n": "none",
    "-p": "none",
    "-r": "none",
    "-s": "none",
    "-t": "none",
    "-u": "none",
    "-v": "none",
    "-w": "none",
    "-x": "none",
}

_COMMANDS: dict[str, CommandConfig] = {
    # Filesystem/content inspection
    "ls": CommandConfig(
        {
            **_GENERIC_VIEW_FLAGS,
            "--all": "none",
            "--long": "none",
            "--human-readable": "none",
        }
    ),
    "cat": CommandConfig(
        {"-n": "none", "-b": "none", "-s": "none", **_SAFE_FLAG_GROUPS["help"]}
    ),
    "head": CommandConfig(
        {
            "-n": "number",
            "-c": "number",
            "-q": "none",
            "-v": "none",
            **_SAFE_FLAG_GROUPS["help"],
        }
    ),
    "tail": CommandConfig(
        {
            "-n": "number",
            "-c": "number",
            "-q": "none",
            "-v": "none",
            **_SAFE_FLAG_GROUPS["help"],
        }
    ),
    "stat": CommandConfig(
        {"-L": "none", "-c": "string", "-f": "none", **_SAFE_FLAG_GROUPS["help"]}
    ),
    "file": CommandConfig(
        {
            "-b": "none",
            "-i": "none",
            "-L": "none",
            "-h": "none",
            "-k": "none",
            "-z": "none",
            **_SAFE_FLAG_GROUPS["help"],
        }
    ),
    "readlink": CommandConfig(
        {
            "-f": "none",
            "-e": "none",
            "-m": "none",
            "-n": "none",
            "-v": "none",
            **_SAFE_FLAG_GROUPS["help"],
        }
    ),
    "realpath": CommandConfig(
        {
            "-e": "none",
            "-m": "none",
            "-L": "none",
            "-P": "none",
            "-q": "none",
            **_SAFE_FLAG_GROUPS["help"],
        }
    ),
    "basename": CommandConfig(_SAFE_FLAG_GROUPS["help"]),
    "dirname": CommandConfig(_SAFE_FLAG_GROUPS["help"]),
    "pwd": CommandConfig(
        {"-L": "none", "-P": "none", **_SAFE_FLAG_GROUPS["help"]},
        allow_positionals=False,
    ),
    "du": CommandConfig(
        {
            "-a": "none",
            "-c": "none",
            "-h": "none",
            "-s": "none",
            "-d": "number",
            "--max-depth": "number",
            **_SAFE_FLAG_GROUPS["help"],
        }
    ),
    "df": CommandConfig(
        {
            "-a": "none",
            "-h": "none",
            "-i": "none",
            "-T": "none",
            "-t": "string",
            "-x": "string",
            **_SAFE_FLAG_GROUPS["help"],
        }
    ),
    "find": CommandConfig(
        {
            "-H": "none",
            "-L": "none",
            "-P": "none",
            "-maxdepth": "number",
            "-mindepth": "number",
            "-name": "string",
            "-iname": "string",
            "-path": "string",
            "-type": "string",
            "-size": "string",
            "-mtime": "string",
            "-mmin": "string",
            "-user": "string",
            "-group": "string",
            "-perm": "string",
            "-readable": "none",
            "-empty": "none",
            "-print": "none",
            "-print0": "none",
        }
    ),
    # Text/log processing
    "grep": CommandConfig(_SAFE_FLAG_GROUPS["grep"]),
    "egrep": CommandConfig(_SAFE_FLAG_GROUPS["grep"]),
    "fgrep": CommandConfig(_SAFE_FLAG_GROUPS["grep"]),
    "sort": CommandConfig(
        {
            "-b": "none",
            "-d": "none",
            "-f": "none",
            "-g": "none",
            "-h": "none",
            "-n": "none",
            "-r": "none",
            "-u": "none",
            "-k": "string",
            "-t": "char",
            **_SAFE_FLAG_GROUPS["help"],
        }
    ),
    "uniq": CommandConfig(
        {
            "-c": "none",
            "-d": "none",
            "-i": "none",
            "-u": "none",
            "-f": "number",
            "-s": "number",
            "-w": "number",
            **_SAFE_FLAG_GROUPS["help"],
        }
    ),
    "cut": CommandConfig(
        {
            "-b": "string",
            "-c": "string",
            "-d": "char",
            "-f": "string",
            "-s": "none",
            **_SAFE_FLAG_GROUPS["help"],
        }
    ),
    "tr": CommandConfig(
        {
            "-c": "none",
            "-d": "none",
            "-s": "none",
            "-t": "none",
            **_SAFE_FLAG_GROUPS["help"],
        }
    ),
    "wc": CommandConfig(
        {
            "-c": "none",
            "-l": "none",
            "-m": "none",
            "-w": "none",
            "-L": "none",
            **_SAFE_FLAG_GROUPS["help"],
        }
    ),
    "sed": CommandConfig(
        {
            "-n": "none",
            "-E": "none",
            "-r": "none",
            "-e": "string",
            "--expression": "string",
            "--quiet": "none",
            "--silent": "none",
            **_SAFE_FLAG_GROUPS["help"],
        }
    ),
    "awk": CommandConfig({"-F": "string", "-v": "string", **_SAFE_FLAG_GROUPS["help"]}),
    "strings": CommandConfig(
        {"-a": "none", "-n": "number", "-t": "string", **_SAFE_FLAG_GROUPS["help"]}
    ),
    "hexdump": CommandConfig(
        {
            "-C": "none",
            "-b": "none",
            "-c": "none",
            "-d": "none",
            "-o": "none",
            "-x": "none",
            "-n": "number",
            "-s": "number",
            **_SAFE_FLAG_GROUPS["help"],
        }
    ),
    "od": CommandConfig(
        {
            "-a": "none",
            "-b": "none",
            "-c": "none",
            "-d": "none",
            "-o": "none",
            "-x": "none",
            "-N": "number",
            "-j": "number",
            "-t": "string",
            **_SAFE_FLAG_GROUPS["help"],
        }
    ),
    "nl": CommandConfig(
        {
            "-b": "string",
            "-n": "string",
            "-s": "string",
            "-w": "number",
            **_SAFE_FLAG_GROUPS["help"],
        }
    ),
    # System/process observation
    "ps": CommandConfig({}, allow_positionals=True),
    "pgrep": CommandConfig(
        {
            "-a": "none",
            "-f": "none",
            "-l": "none",
            "-u": "string",
            "-x": "none",
            **_SAFE_FLAG_GROUPS["help"],
        }
    ),
    "top": CommandConfig(
        {
            "-b": "none",
            "-n": "number",
            "-d": "number",
            "-p": "string",
            **_SAFE_FLAG_GROUPS["help"],
        }
    ),
    "free": CommandConfig(
        {
            "-b": "none",
            "-k": "none",
            "-m": "none",
            "-g": "none",
            "-h": "none",
            "-s": "number",
            **_SAFE_FLAG_GROUPS["help"],
        }
    ),
    "uptime": CommandConfig(
        {"-p": "none", "-s": "none", **_SAFE_FLAG_GROUPS["help"]},
        allow_positionals=False,
    ),
    "who": CommandConfig(
        {
            "-a": "none",
            "-b": "none",
            "-d": "none",
            "-H": "none",
            "-q": "none",
            "-r": "none",
            "-u": "none",
            **_SAFE_FLAG_GROUPS["help"],
        }
    ),
    "w": CommandConfig(
        {"-h": "none", "-s": "none", "-f": "none", **_SAFE_FLAG_GROUPS["help"]}
    ),
    "last": CommandConfig(
        {"-n": "number", "-F": "none", "-x": "none", **_SAFE_FLAG_GROUPS["help"]}
    ),
    "dmesg": CommandConfig(
        {
            "-H": "none",
            "-T": "none",
            "-l": "string",
            "-p": "none",
            "-x": "none",
            **_SAFE_FLAG_GROUPS["help"],
        }
    ),
    "journalctl": CommandConfig(
        {
            "-b": "string",
            "-u": "string",
            "-p": "string",
            "-n": "number",
            "-f": "none",
            "--no-pager": "none",
            "--since": "string",
            "--until": "string",
            "-o": "string",
            **_SAFE_FLAG_GROUPS["help"],
        }
    ),
    "systemctl": CommandConfig(
        {
            "--type": "string",
            "--state": "string",
            "--no-pager": "none",
            "--failed": "none",
            "-l": "none",
            **_SAFE_FLAG_GROUPS["help"],
        }
    ),
    "service": CommandConfig({}, allow_positionals=True),
    "lsof": CommandConfig(
        {
            "-i": "string",
            "-p": "string",
            "-u": "string",
            "-n": "none",
            "-P": "none",
            **_SAFE_FLAG_GROUPS["help"],
        }
    ),
    # Network observation only
    "ss": CommandConfig(
        {
            **_GENERIC_VIEW_FLAGS,
            "-4": "none",
            "-6": "none",
            "-A": "string",
            "-o": "none",
            "-O": "none",
        }
    ),
    "netstat": CommandConfig({**_GENERIC_VIEW_FLAGS, "-4": "none", "-6": "none"}),
    "ip": CommandConfig(
        {
            "-4": "none",
            "-6": "none",
            "-o": "none",
            "-s": "none",
            "-d": "none",
            "-br": "none",
            "-j": "none",
            "-p": "none",
        }
    ),
    "hostname": CommandConfig(
        {
            "-f": "none",
            "-i": "none",
            "-I": "none",
            "-s": "none",
            **_SAFE_FLAG_GROUPS["help"],
        },
        allow_positionals=False,
    ),
    # Basic facts
    "echo": CommandConfig({"-n": "none", "-e": "none", "-E": "none"}),
    "printf": CommandConfig({}, allow_positionals=True),
    "which": CommandConfig({"-a": "none", **_SAFE_FLAG_GROUPS["help"]}),
    "type": CommandConfig({"-a": "none", "-p": "none", "-P": "none", "-t": "none"}),
    "uname": CommandConfig({**_GENERIC_VIEW_FLAGS, "-o": "none"}),
    "date": CommandConfig(
        {
            "-u": "none",
            "-R": "none",
            "-I": "string",
            "--iso-8601": "string",
            **_SAFE_FLAG_GROUPS["help"],
        }
    ),
    "id": CommandConfig(
        {
            "-a": "none",
            "-g": "none",
            "-G": "none",
            "-n": "none",
            "-r": "none",
            "-u": "none",
            "-Z": "none",
            **_SAFE_FLAG_GROUPS["help"],
        }
    ),
    "groups": CommandConfig(_SAFE_FLAG_GROUPS["help"]),
    # Git diagnostics only; no fetch/push/reset/clean/checkout.
    "git status": CommandConfig(
        {
            "--short": "none",
            "-s": "none",
            "--branch": "none",
            "-b": "none",
            "--porcelain": "string",
            "--ignored": "none",
            "-u": "string",
        }
    ),
    "git log": CommandConfig(
        {
            "--oneline": "none",
            "--graph": "none",
            "--decorate": "none",
            "--no-decorate": "none",
            "--stat": "none",
            "--name-only": "none",
            "--max-count": "number",
            "-n": "number",
            "--since": "string",
            "--until": "string",
            "--author": "string",
            "--grep": "string",
            "--format": "string",
            "--pretty": "string",
            "-p": "none",
        }
    ),
    "git show": CommandConfig(
        {
            "--stat": "none",
            "--name-only": "none",
            "--name-status": "none",
            "--format": "string",
            "--pretty": "string",
            "--no-patch": "none",
            "-s": "none",
            "-p": "none",
        }
    ),
    "git diff": CommandConfig(
        {
            "--stat": "none",
            "--name-only": "none",
            "--name-status": "none",
            "--check": "none",
            "--cached": "none",
            "--staged": "none",
            "--color": "string",
            "--no-color": "none",
            "-p": "none",
            "-u": "none",
            "-U": "number",
        }
    ),
    "git branch": CommandConfig(
        {
            "--list": "none",
            "-l": "none",
            "-a": "none",
            "-r": "none",
            "-v": "none",
            "-vv": "none",
            "--contains": "string",
            "--merged": "string",
            "--no-merged": "string",
        }
    ),
}

_DANGEROUS_COMMANDS: set[str] = {
    "rm",
    "rmdir",
    "mv",
    "cp",
    "touch",
    "mkdir",
    "chmod",
    "chown",
    "chgrp",
    "ln",
    "dd",
    "mkfs",
    "mount",
    "umount",
    "kill",
    "pkill",
    "killall",
    "reboot",
    "shutdown",
    "sudo",
    "su",
    "doas",
    "bash",
    "sh",
    "zsh",
    "dash",
    "eval",
    "exec",
    "source",
    ".",
    "xargs",
    "tee",
    "curl",
    "wget",
    "nc",
    "ncat",
    "netcat",
    "ssh",
    "scp",
    "rsync",
    "ftp",
    "sftp",
    "telnet",
    "python",
    "python3",
    "perl",
    "ruby",
    "node",
    "php",
    "kubectl",
    "docker",
    "podman",
    "terraform",
    "ansible",
    "mysql",
    "psql",
    "sqlite3",
}

_SENSITIVE_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^/etc/(shadow|gshadow|sudoers)(?:$|/)"),
    re.compile(r"^/etc/.*(?:secret|token|credential|passwd|key)", re.I),
    re.compile(r"^/(?:root|home/[^/]+)/\.ssh(?:$|/)"),
    re.compile(
        r"^/(?:root|home/[^/]+)/\.(?:aws|azure|gnupg|kube|docker|config/gcloud)(?:$|/)"
    ),
    re.compile(r"^/proc/(?:self|\d+|[^/]+)/environ$"),
    re.compile(r"^/proc/.*/cmdline$"),
)

_DANGEROUS_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"`"),
    re.compile(r"\$\("),
    re.compile(r"\$\{"),
    re.compile(r"\$\["),
    re.compile(r"<\("),
    re.compile(r">\("),
    re.compile(r"\$IFS|\$\{[^}]*IFS"),
    re.compile(r"(?:^|[\s;&|])=[A-Za-z_]"),  # zsh equals expansion
    re.compile(r"<#"),  # PowerShell comment syntax, defense-in-depth
)

_DANGEROUS: set[str] = {
    "file_redirect",
    "heredoc_redirect",
    "herestring_redirect",
    "command_substitution",
    "process_substitution",
    "subshell",
    "compound_statement",
    "for_statement",
    "while_statement",
    "until_statement",
    "if_statement",
    "case_statement",
    "function_definition",
    "test_command",
    "ansi_c_string",
    "translated_string",
    "brace_expression",
    "do_group",
    "elif_clause",
    "else_clause",
}

_STRUCTURAL: set[str] = {
    "program",
    "list",
    "pipeline",
    "redirected_statement",
    "command",
    "negated_command",
    "declaration_command",
    "variable_assignment",
}

_ARGUMENT_TYPES: set[str] = {
    "command_name",
    "word",
    "number",
    "string",
    "raw_string",
    "concatenation",
    "variable_name",
    "simple_expansion",
    "expansion",
    "comment",
    "string_content",
    '"',
    "'",
    "$",
}

_SEPARATORS: set[str] = {"&&", "||", "|", ";", "&", "|&", "\n", ";;", ";;&"}
_ALLOWED: set[str] = _STRUCTURAL | _ARGUMENT_TYPES | _SEPARATORS | _DANGEROUS
_parser_cache: Parser | None = None


def classify_bash(command: str) -> dict:
    """Return {"safe": bool}; safe means read-only enough to skip approval."""
    if not command or not command.strip():
        return {"safe": False}

    try:
        safe = (
            _tree_sitter_safe(command)
            and _text_security_safe(command)
            and _segments_safe(command)
        )
        return {"safe": safe}
    except Exception:
        return {"safe": False}


def _tree_sitter_safe(command: str) -> bool:
    if not _tree_sitter_available():
        return False
    parser = _get_parser()
    root = parser.parse(command.encode("utf-8")).root_node
    return not _has_error_node(root) and _walk(root)


def _tree_sitter_available() -> bool:
    try:
        import tree_sitter  # noqa: F401
        import tree_sitter_bash  # noqa: F401

        return True
    except ImportError:
        return False


def _get_parser():
    global _parser_cache
    if _parser_cache is not None:
        return _parser_cache
    from tree_sitter import Language, Parser
    from tree_sitter_bash import language as get_language_ptr

    parser = Parser()
    parser.language = Language(get_language_ptr())
    _parser_cache = parser
    return parser


def _walk(node) -> bool:
    if node.type not in _ALLOWED:
        return False
    if node.type in _DANGEROUS:
        return False
    for child in node.children:
        if child is not None and not _walk(child):
            return False
    return True


def _has_error_node(node) -> bool:
    return node.type == "ERROR" or any(
        child is not None and _has_error_node(child) for child in node.children
    )


def _text_security_safe(command: str) -> bool:
    if command.lstrip().startswith(("-", "&&", "||", ";", ">", "<", "|")):
        return False
    if "\r" in command or _has_unsafe_newline(command):
        return False
    if any(pattern.search(command) for pattern in _DANGEROUS_TEXT_PATTERNS):
        return False
    if re.search(r"[<>]", _strip_quoted_content(command)):
        return False
    return True


def _has_unsafe_newline(command: str) -> bool:
    if "\n" not in command:
        return False
    return bool(re.search(r"(?<![ \t]\\)\n\s*\S", command))


def _strip_quoted_content(command: str) -> str:
    result: list[str] = []
    state = _ShellQuoteState()
    for char in command:
        was_quoted = state.is_quoted
        action = state.consume(char)
        if action == "quote":
            continue
        if not was_quoted and action in {"escape", "text"}:
            result.append(char)
    return "".join(result)


def _segments_safe(command: str) -> bool:
    for segment in _split_command_segments(command):
        if not segment.strip():
            continue
        tokens = _tokenize(segment)
        if not tokens or not _command_tokens_safe(tokens):
            return False
    return True


def _split_command_segments(command: str) -> list[str]:
    segments: list[str] = []
    current: list[str] = []
    state = _ShellQuoteState()
    i = 0
    while i < len(command):
        split_width = _segment_split_width(command, i, state)
        if split_width:
            segments.append("".join(current))
            current = []
            i += split_width
            continue
        char = command[i]
        state.consume(char)
        current.append(char)
        i += 1
    segments.append("".join(current))
    return segments


def _segment_split_width(command: str, index: int, state: _ShellQuoteState) -> int:
    if state.is_quoted or state.escaped:
        return 0
    if command.startswith(("&&", "||", "|&"), index):
        return 2
    if command[index] in "|;":
        return 1
    return 0


def _tokenize(segment: str) -> list[str]:
    lexer = shlex.shlex(segment, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def _command_tokens_safe(tokens: list[str]) -> bool:
    tokens = _strip_env_assignments(tokens)
    if not tokens:
        return False
    if "/" in tokens[0]:
        return False

    pattern, config = _match_command_config(tokens)
    if pattern is None or config is None:
        return False

    name = tokens[0].rsplit("/", 1)[-1]
    if name in _DANGEROUS_COMMANDS:
        return False

    command_token_count = len(pattern.split())
    args = tokens[command_token_count:]
    if any(_token_has_unsafe_expansion(token) for token in args):
        return False
    if not _validate_flags(args, config, name):
        return False
    if _command_specific_dangerous(pattern, args):
        return False
    return _paths_safe(_extract_path_like_args(pattern, args))


def _strip_env_assignments(tokens: list[str]) -> list[str]:
    i = 0
    while i < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", tokens[i]):
        i += 1
    return tokens[i:]


def _match_command_config(tokens: list[str]) -> tuple[str | None, CommandConfig | None]:
    normalized = [tokens[0], *tokens[1:]]
    for pattern, config in sorted(
        _COMMANDS.items(), key=lambda item: len(item[0].split()), reverse=True
    ):
        parts = pattern.split()
        if len(normalized) >= len(parts) and normalized[: len(parts)] == parts:
            return pattern, config
    return None, None


def _token_has_unsafe_expansion(token: str) -> bool:
    if "$" not in token:
        return False
    return not re.fullmatch(r"(?:[^$]|\$[A-Za-z_][A-Za-z0-9_]*)+", token)


def _validate_flags(args: list[str], config: CommandConfig, command_name: str) -> bool:
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--" and config.respects_double_dash:
            return _positionals_safe(args[i + 1 :], config)
        if arg.startswith("-") and len(arg) > 1 and re.match(r"^-[A-Za-z0-9_-]", arg):
            if not _validate_one_flag(args, i, config, command_name):
                return False
            i += _flag_width(args, i, config)
            continue
        if not config.allow_positionals:
            return False
        i += 1
    return True


def _validate_one_flag(
    args: list[str], i: int, config: CommandConfig, command_name: str
) -> bool:
    flag, has_equals, inline = args[i].partition("=")
    flag_type = config.safe_flags.get(flag)
    if flag_type is None:
        return _unknown_flag_safe(flag, config, command_name)
    if flag_type == "none":
        return not has_equals
    value = _flag_value(args, i, inline, has_equals)
    if _missing_or_unsafe_flag_value(value, flag_type, has_equals):
        return False
    return _validate_flag_arg(value, flag_type)


def _unknown_flag_safe(flag: str, config: CommandConfig, command_name: str) -> bool:
    if command_name in {"grep", "egrep", "fgrep"} and _is_attached_context_flag(
        flag, config
    ):
        return True
    return _is_safe_bundled_flags(flag, config)


def _flag_value(args: list[str], i: int, inline: str, has_equals: str) -> str:
    if has_equals:
        return inline
    return args[i + 1] if i + 1 < len(args) else ""


def _missing_or_unsafe_flag_value(
    value: str, flag_type: FlagArgType, has_equals: str
) -> bool:
    if not has_equals and (not value or _looks_like_flag(value)):
        return True
    return flag_type == "string" and value.startswith("-")


def _flag_width(args: list[str], i: int, config: CommandConfig) -> int:
    flag = args[i].split("=", 1)[0]
    flag_type = config.safe_flags.get(flag)
    if flag_type and flag_type != "none" and "=" not in args[i]:
        return 2
    return 1


def _looks_like_flag(value: str) -> bool:
    return (
        value.startswith("-")
        and len(value) > 1
        and bool(re.match(r"^-[A-Za-z0-9_-]", value))
    )


def _is_attached_context_flag(flag: str, config: CommandConfig) -> bool:
    return (
        len(flag) > 2
        and flag[:2] in config.safe_flags
        and config.safe_flags[flag[:2]] == "number"
        and flag[2:].isdigit()
    )


def _is_safe_bundled_flags(flag: str, config: CommandConfig) -> bool:
    if flag.startswith("--") or len(flag) <= 2:
        return False
    return all(config.safe_flags.get(f"-{char}") == "none" for char in flag[1:])


def _validate_flag_arg(value: str, flag_type: FlagArgType) -> bool:
    if flag_type == "number":
        return bool(re.fullmatch(r"\d+", value))
    if flag_type == "char":
        return len(value) == 1
    if flag_type == "string":
        return True
    return False


def _positionals_safe(positionals: list[str], config: CommandConfig) -> bool:
    return config.allow_positionals and all(
        not _looks_like_flag(token) for token in positionals
    )


def _command_specific_dangerous(pattern: str, args: list[str]) -> bool:
    checker = _COMMAND_DANGER_CHECKS.get(pattern)
    if checker is not None:
        return checker(pattern, args)
    return _git_command_dangerous(args) if pattern.startswith("git ") else False


def _sed_dangerous(_pattern: str, args: list[str]) -> bool:
    scripts = _sed_scripts(args)
    if scripts is None:
        return True
    return any(not _sed_script_safe(script) for script in scripts)


def _sed_scripts(args: list[str]) -> list[str] | None:
    scripts: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"-f", "--file"} or arg.startswith("--file="):
            return None
        if arg in {"-e", "--expression"}:
            if i + 1 >= len(args):
                return None
            scripts.append(args[i + 1])
            i += 2
            continue
        if arg.startswith("--expression="):
            scripts.append(arg.split("=", 1)[1])
            i += 1
            continue
        if _looks_like_flag(arg):
            i += 1
            continue
        scripts.append(arg)
        break
    return scripts or None


def _sed_script_safe(script: str) -> bool:
    commands = [part.strip() for part in script.split(";") if part.strip()]
    return bool(commands) and all(_sed_command_safe(command) for command in commands)


def _sed_command_safe(command: str) -> bool:
    return _sed_print_command_safe(command) or _sed_substitute_command_safe(command)


def _sed_print_command_safe(command: str) -> bool:
    return bool(re.fullmatch(r"(?:\d+(?:,\d+)?|\$)?p", command))


def _sed_substitute_command_safe(command: str) -> bool:
    match = re.match(r"^(?:(?:\d+(?:,\d+)?|\$)\s*)?s(?P<delim>\S)", command)
    if not match:
        return False
    flags = _sed_substitute_flags(command[match.end() :], match.group("delim"))
    return flags is not None and bool(re.fullmatch(r"[0-9gIpM]*", flags))


def _sed_substitute_flags(rest: str, delim: str) -> str | None:
    delimiter_count = 0
    escaped = False
    for index, char in enumerate(rest):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == delim:
            delimiter_count += 1
            if delimiter_count == 2:
                return rest[index + 1 :].strip()
    return None


def _awk_dangerous(_pattern: str, args: list[str]) -> bool:
    return any(_awk_program_dangerous(arg) for arg in args)


def _awk_program_dangerous(arg: str) -> bool:
    scrubbed = _strip_quoted_content(arg)
    return bool(
        re.search(r"\b(system|getline)\b", scrubbed)
        or re.search(r"\b(?:print|printf)\b[^|;\n]*>", scrubbed)
        or re.search(r"\|&?|\bfflush\s*\(", scrubbed)
    )


def _find_dangerous(_pattern: str, args: list[str]) -> bool:
    return any(
        arg
        in {
            "-exec",
            "-execdir",
            "-delete",
            "-ok",
            "-okdir",
            "-fls",
            "-fprint",
            "-fprintf",
        }
        for arg in args
    )


def _ps_dangerous(_pattern: str, args: list[str]) -> bool:
    joined = " ".join(args)
    return bool(
        re.search(r"\b(?:e|auxe|axe)\b", joined)
        or re.search(r"-[A-Za-z]*e[A-Za-z]*", joined)
    )


def _journalctl_dangerous(_pattern: str, args: list[str]) -> bool:
    return "--file" in args or "-D" in args


def _systemctl_dangerous(pattern: str, args: list[str]) -> bool:
    subcommand = _first_positional_arg(pattern, args)
    return subcommand not in {
        "status",
        "show",
        "list-units",
        "list-unit-files",
        "is-active",
        "is-enabled",
        "cat",
    }


def _service_dangerous(_pattern: str, args: list[str]) -> bool:
    return len(args) < 2 or args[1] != "status"


def _ip_dangerous(_pattern: str, args: list[str]) -> bool:
    read_only_objects = {
        "addr",
        "address",
        "link",
        "route",
        "neigh",
        "netns",
        "rule",
        "maddr",
        "monitor",
    }
    global_flags = {"-br", "-j", "-o", "-s", "-d", "-4", "-6"}
    return bool(args and args[0] not in read_only_objects | global_flags)


def _git_command_dangerous(args: list[str]) -> bool:
    return any(
        arg in {"--output", "--output="} or arg.startswith("--output=") for arg in args
    )


_COMMAND_DANGER_CHECKS = {
    "sed": _sed_dangerous,
    "awk": _awk_dangerous,
    "find": _find_dangerous,
    "ps": _ps_dangerous,
    "journalctl": _journalctl_dangerous,
    "systemctl": _systemctl_dangerous,
    "service": _service_dangerous,
    "ip": _ip_dangerous,
}


def _first_positional_arg(pattern: str, args: list[str]) -> str | None:
    flags = _COMMANDS.get(pattern, CommandConfig({})).safe_flags
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            return args[i + 1] if i + 1 < len(args) else None
        if _looks_like_flag(arg):
            flag = arg.split("=", 1)[0]
            if "=" not in arg and flags.get(flag) not in {None, "none"}:
                i += 2
                continue
            i += 1
            continue
        return arg
    return None


def _extract_path_like_args(pattern: str, args: list[str]) -> list[str]:
    if pattern in {
        "echo",
        "printf",
        "date",
        "uname",
        "uptime",
        "who",
        "w",
        "id",
        "groups",
        "hostname",
        "pwd",
    }:
        return []
    paths: list[str] = []
    skip_next = False
    flags_with_args = _COMMANDS.get(pattern, CommandConfig({})).safe_flags
    for i, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if arg == "--":
            paths.extend(args[i + 1 :])
            break
        if _looks_like_flag(arg):
            flag = arg.split("=", 1)[0]
            if "=" not in arg and flags_with_args.get(flag) not in {None, "none"}:
                skip_next = True
            continue
        if "/" in arg or arg.startswith((".", "~")):
            paths.append(arg)
    return paths


def _paths_safe(paths: list[str]) -> bool:
    return all(_path_safe(path) for path in paths)


def _path_safe(path: str) -> bool:
    stripped = path.strip("\"'")
    if "$" in stripped:
        return False
    if (
        stripped.startswith("~")
        and stripped not in {"~"}
        and not stripped.startswith("~/")
    ):
        return False
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", stripped):
        return False
    expanded = _expand_home(stripped)
    normalized = _normalize_posix_path(expanded)
    if any(pattern.search(normalized) for pattern in _SENSITIVE_PATH_PATTERNS):
        return False
    return True


def _expand_home(path: str) -> str:
    if path == "~":
        return "/home/user"
    if path.startswith("~/"):
        return "/home/user/" + path[2:]
    return path


def _normalize_posix_path(path: str) -> str:
    try:
        normalized = posixpath.normpath(str(PurePosixPath(path)))
        if path.startswith("/") and not normalized.startswith("/"):
            return f"/{normalized}"
        return normalized
    except Exception:
        return path
