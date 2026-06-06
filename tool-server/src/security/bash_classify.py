"""Tree-sitter-based bash command classifier. FAIL-CLOSED design.

Parse the command with tree-sitter-bash, walk the AST, and check every node
type against an explicit allowlist. Any unrecognized node type, parse error,
or import failure results in {"safe": False}.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Parser

# ── Safe read-only command names ──────────────────────────────────────────

_SAFE_COMMANDS: set[str] = {
    "ls", "cat", "head", "tail", "grep", "find", "wc", "sort",
    "uniq", "cut", "tr", "du", "df", "ps", "free", "uptime",
    "who", "w", "last", "dmesg", "journalctl",
    "echo", "printf", "pwd", "which", "type", "uname", "hostname",
    "date", "env", "printenv", "id", "groups", "stat", "file",
    "readlink", "realpath", "basename", "dirname",
}

# ── Node type allowlist ───────────────────────────────────────────────────

# Structural types: we recurse into children. These wrap other commands.
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

# Leaf/argument types that are safe inside a command.
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
    "\"",  # literal double-quote delimiter in string nodes
    "'",   # literal single-quote delimiter in string nodes
    "$",   # dollar-sign token in variable expansions
}

# Separator tokens between commands — benign.
_SEPARATORS: set[str] = {
    "&&", "||", "|", ";", "&", "|&", "\n", ";;", ";;&",
}

# Types that are always dangerous. No safe command uses these.
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

# All types we understand. Anything NOT in this union → FAIL-CLOSED.
_ALLOWED: set[str] = _STRUCTURAL | _ARGUMENT_TYPES | _SEPARATORS | _DANGEROUS


def _tree_sitter_available() -> bool:
    try:
        import tree_sitter  # noqa: F401
        import tree_sitter_bash  # noqa: F401
        return True
    except ImportError:
        return False


# Module-level cached parser — tree-sitter Language loading is expensive.
_parser_cache: Parser | None = None


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


def _walk(node, unsafe_commands: set[str]) -> bool:
    """Walk tree-sitter AST node. Return True if safe, False if dangerous.

    FAIL-CLOSED: any unrecognized node type → safe=False.
    """
    node_type = node.type

    # Unrecognized type → FAIL-CLOSED
    if node_type not in _ALLOWED:
        return False

    # Dangerous structural types → unsafe
    if node_type in _DANGEROUS:
        return False

    # Check command_name against the safe list
    if node_type == "command_name":
        text = node.text.decode("utf-8").strip()
        # Strip leading paths: /usr/bin/ls → ls
        name = text.rsplit("/", 1)[-1] if "/" in text else text
        if name not in _SAFE_COMMANDS:
            unsafe_commands.add(name)
            return False

    # Recurse into children
    for child in node.children:
        if child is not None:
            if not _walk(child, unsafe_commands):
                return False

    return True


def classify_bash(command: str) -> dict:
    """Parse bash command with tree-sitter, return {"safe": bool}.

    FAIL-CLOSED design:
    - Empty command → safe=False
    - tree-sitter not available → safe=False
    - Parse error → safe=False
    - Unknown AST node type → safe=False
    - Any dangerous construct (redirect, substitution, etc.) → safe=False
    - Command name not in safe list → safe=False

    Only returns safe=True when ALL of:
    1. tree-sitter parses successfully
    2. Every node type is in our allowlist
    3. No dangerous structural types present
    4. All command names are in the safe read-only list
    """
    if not command or not command.strip():
        return {"safe": False}

    if not _tree_sitter_available():
        return {"safe": False}

    try:
        parser = _get_parser()

        tree = parser.parse(command.encode("utf-8"))
        root = tree.root_node

        # Check for ERROR nodes at any depth — parse failure
        if _has_error_node(root):
            return {"safe": False}

        unsafe_commands: set[str] = set()
        safe = _walk(root, unsafe_commands)

        return {"safe": safe}
    except Exception:
        # Any exception → FAIL-CLOSED
        return {"safe": False}


def _has_error_node(node) -> bool:
    """Check if any node in the tree is an ERROR node."""
    if node.type == "ERROR":
        return True
    for child in node.children:
        if child is not None and _has_error_node(child):
            return True
    return False
