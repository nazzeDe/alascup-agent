import json

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.unit


@pytest.fixture
def tmp_config_dir(tmp_path):
    return tmp_path


class TestLLMConfig:
    def test_load_minimal(self, tmp_config_dir):
        from src.config.loader import load_llm_config

        cfg = {"api_key": "sk-test", "api_url": "https://api.example.com/v1", "model": "deepseek-v4"}
        (tmp_config_dir / "llm.json").write_text(json.dumps(cfg))

        result = load_llm_config(tmp_config_dir / "llm.json")
        assert result.api_key == "sk-test"
        assert result.model == "deepseek-v4"
        assert result.summary_model is None

    def test_load_with_summary_model(self, tmp_config_dir):
        from src.config.loader import load_llm_config

        cfg = {"api_key": "sk-test", "api_url": "https://api.example.com/v1", "model": "deepseek-v4-pro", "summary_model": "deepseek-v4-flash"}
        (tmp_config_dir / "llm.json").write_text(json.dumps(cfg))

        result = load_llm_config(tmp_config_dir / "llm.json")
        assert result.summary_model == "deepseek-v4-flash"

    def test_file_not_found(self, tmp_config_dir):
        from src.config.loader import load_llm_config

        with pytest.raises(FileNotFoundError):
            load_llm_config(tmp_config_dir / "nonexistent.json")


class TestRulesConfig:
    def test_load_rules(self, tmp_config_dir):
        from src.config.loader import load_rules_config

        cfg = {
            "whitelist": [{"tool_name": "delete_temp_files", "description": "safe"}],
            "blacklist": [{"tool_name": "reboot_system", "description": "dangerous"}],
        }
        (tmp_config_dir / "rules.json").write_text(json.dumps(cfg))

        result = load_rules_config(tmp_config_dir / "rules.json")
        assert len(result.whitelist) == 1
        assert result.whitelist[0].tool_name == "delete_temp_files"
        assert len(result.blacklist) == 1

    def test_load_empty_rules(self, tmp_config_dir):
        from src.config.loader import load_rules_config

        (tmp_config_dir / "rules.json").write_text(json.dumps({}))

        result = load_rules_config(tmp_config_dir / "rules.json")
        assert result.whitelist == []
        assert result.blacklist == []

    def test_file_not_found_returns_default(self, tmp_config_dir):
        from src.config.loader import load_rules_config

        result = load_rules_config(tmp_config_dir / "nonexistent.json")
        assert result.whitelist == []
        assert result.blacklist == []


class TestServersConfig:
    def test_load_servers(self, tmp_config_dir):
        from src.config.loader import load_servers_config

        cfg = [
            {"name": "tool-server", "url": "http://tool-server:8001"},
            {"name": "rag-server", "url": "http://rag-server:8002", "transport": "streamable-http"},
        ]
        (tmp_config_dir / "servers.json").write_text(json.dumps(cfg))

        result = load_servers_config(tmp_config_dir / "servers.json")
        assert len(result) == 2
        assert result[0].name == "tool-server"
        assert result[0].url == "http://tool-server:8001"
        assert result[0].transport == "streamable-http"
        assert result[1].name == "rag-server"
        assert result[1].url == "http://rag-server:8002"

    def test_load_empty_servers(self, tmp_config_dir):
        from src.config.loader import load_servers_config

        (tmp_config_dir / "servers.json").write_text(json.dumps([]))
        result = load_servers_config(tmp_config_dir / "servers.json")
        assert result == []

    def test_missing_required_field_raises(self, tmp_config_dir):
        from src.config.loader import load_servers_config

        cfg = [{"name": "tool-server"}]
        (tmp_config_dir / "servers.json").write_text(json.dumps(cfg))

        with pytest.raises(ValidationError):
            load_servers_config(tmp_config_dir / "servers.json")

    def test_duplicate_name_raises(self, tmp_config_dir):
        from src.config.loader import load_servers_config

        cfg = [
            {"name": "tool-server", "url": "http://a:8001"},
            {"name": "tool-server", "url": "http://b:8001"},
        ]
        (tmp_config_dir / "servers.json").write_text(json.dumps(cfg))

        with pytest.raises(ValueError, match="Duplicate server name"):
            load_servers_config(tmp_config_dir / "servers.json")

    def test_file_not_found(self, tmp_config_dir):
        from src.config.loader import load_servers_config

        with pytest.raises(FileNotFoundError):
            load_servers_config(tmp_config_dir / "nonexistent.json")


class TestWebServerConfig:
    def test_config_creation(self):
        from src.config.models import WebServerConfig

        cfg = WebServerConfig(
            context_window_size=128000,
            tool_request_timeout_seconds=300,
        )
        assert cfg.context_window_size == 128000
        assert cfg.tool_request_timeout_seconds == 300
