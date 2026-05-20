import json
from pathlib import Path

from pydantic import TypeAdapter

from src.config.models import LLMConfig, RulesConfig, ServerEntry


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


_servers_adapter = TypeAdapter(list[ServerEntry])


def load_servers_config(path: Path) -> list[ServerEntry]:
    data = _read_json(path)
    servers = _servers_adapter.validate_python(data)
    names = [s.name for s in servers]
    if len(names) != len(set(names)):
        seen = set()
        for n in names:
            if n in seen:
                raise ValueError(f"Duplicate server name: {n}")
            seen.add(n)
    return servers
