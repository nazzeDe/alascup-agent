import json

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.unit


@pytest.fixture
def tmp_config_dir(tmp_path):
    return tmp_path


class TestLLMConfig:
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
        ]
        (tmp_config_dir / "servers.json").write_text(json.dumps(cfg))

        result = load_servers_config(tmp_config_dir / "servers.json")
        assert len(result) == 1
        assert result[0].name == "tool-server"
        assert result[0].url == "http://tool-server:8001"
        assert result[0].transport == "streamable-http"

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


class TestSettings:
    def test_defaults(self):
        from src.config.models import Settings

        cfg = Settings()
        assert cfg.context_window_size == 128000
        assert cfg.tool_request_timeout_seconds == 300
        assert cfg.agent_max_iterations == 30
        assert cfg.log_level == "INFO"

    def test_llm_config_property(self):
        from src.config.models import Settings

        cfg = Settings(
            llm_api_key="sk-test",
            llm_api_url="https://api.example.com",
            llm_model="test-model",
            llm_max_tokens=4096,
        )
        llm = cfg.llm_config
        assert llm.api_key == "sk-test"
        assert llm.api_url == "https://api.example.com"
        assert llm.model == "test-model"
        assert llm.max_tokens == 4096

    def test_env_prefix(self, monkeypatch):
        shared_token = "test-" + "token"
        monkeypatch.setenv("DATABASE_URL", "postgresql://env/db")
        monkeypatch.setenv("ALASCUP_LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("TOOLSERVER_SHARED_SECRET", shared_token)

        from src.config.models import Settings

        cfg = Settings()
        assert cfg.database_url == "postgresql://env/db"
        assert cfg.log_level == "DEBUG"
        assert cfg.toolserver_shared_secret == shared_token

    def test_kwarg_overrides_env(self, monkeypatch):
        monkeypatch.setenv("ALASCUP_LOG_LEVEL", "DEBUG")

        from src.config.models import Settings

        cfg = Settings(log_level="ERROR")
        assert cfg.log_level == "ERROR"
