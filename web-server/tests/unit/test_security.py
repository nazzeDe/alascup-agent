import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def rules_config():
    from src.config.models import RulesConfig, RuleEntry

    return RulesConfig(
        whitelist=[RuleEntry(tool_name="delete_temp_files", description="safe")],
        blacklist=[RuleEntry(tool_name="reboot_system", description="dangerous")],
    )


@pytest.fixture
def read_only_tool():
    from src.models.tool import Tool, ServerName

    return Tool(
        name="get_cpu_info",
        server=ServerName.TOOL_SERVER,
        description="get cpu",
        is_read_only=True,
        is_rollbackable=False,
        params_schema={},
    )


@pytest.fixture
def high_risk_tool():
    from src.models.tool import Tool, ServerName

    return Tool(
        name="delete_temp_files",
        server=ServerName.TOOL_SERVER,
        description="clean temp",
        is_read_only=False,
        is_rollbackable=True,
        params_schema={},
    )


@pytest.fixture
def blacklisted_tool():
    from src.models.tool import Tool, ServerName

    return Tool(
        name="reboot_system",
        server=ServerName.TOOL_SERVER,
        description="reboot",
        is_read_only=False,
        is_rollbackable=False,
        params_schema={},
    )


class TestRuleEngine:
    def test_read_only_auto_approved(self, rules_config):
        from src.security.rule_engine import RuleEngine

        engine = RuleEngine(rules_config)
        decision = engine.evaluate(
            "get_cpu_info", is_read_only=True, is_rollbackable=False
        )
        assert decision == "AUTO_APPROVE"

    def test_blacklist_rejected(self, rules_config):
        from src.security.rule_engine import RuleEngine

        engine = RuleEngine(rules_config)
        decision = engine.evaluate(
            "reboot_system", is_read_only=False, is_rollbackable=False
        )
        assert decision == "REJECT"

    def test_whitelist_override(self, rules_config):
        from src.security.rule_engine import RuleEngine

        engine = RuleEngine(rules_config)
        decision = engine.evaluate(
            "delete_temp_files", is_read_only=False, is_rollbackable=True
        )
        assert decision == "AUTO_APPROVE"

    def test_high_risk_requires_approval(self, rules_config):
        from src.security.rule_engine import RuleEngine

        engine = RuleEngine(rules_config)
        decision = engine.evaluate("rm_rf", is_read_only=False, is_rollbackable=False)
        assert decision == "REQUIRE_APPROVAL"

    def test_classification_overrides_static_readonly(self, rules_config):
        from src.security.rule_engine import RuleEngine

        engine = RuleEngine(rules_config)
        decision = engine.evaluate("bash", is_read_only=False, is_rollbackable=False)
        assert decision == "REQUIRE_APPROVAL"
