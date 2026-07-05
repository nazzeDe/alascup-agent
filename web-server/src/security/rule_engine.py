from src.config.models import RulesConfig


class RuleEngine:
    def __init__(self, rules: RulesConfig) -> None:
        self._blacklist = {r.tool_name for r in rules.blacklist}
        self._whitelist = {r.tool_name for r in rules.whitelist}

    def evaluate(
        self, tool_name: str, is_read_only: bool, is_rollbackable: bool
    ) -> str:
        if tool_name in self._blacklist:
            return "REJECT"
        if tool_name in self._whitelist:
            return "AUTO_APPROVE"
        if is_read_only:
            return "AUTO_APPROVE"
        return "REQUIRE_APPROVAL"
