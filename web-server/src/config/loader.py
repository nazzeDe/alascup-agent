import json
from pathlib import Path

from src.config.models import LLMConfig, RulesConfig, ServersConfig


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"config file not found: {path}")
    return json.loads(path.read_text())


def load_llm_config(path: Path) -> LLMConfig:
    return LLMConfig.model_validate(_read_json(path))


def load_rules_config(path: Path) -> RulesConfig:
    try:
        return RulesConfig.model_validate(_read_json(path))
    except FileNotFoundError:
        return RulesConfig()


def load_servers_config(path: Path) -> ServersConfig:
    return ServersConfig.model_validate(_read_json(path))
