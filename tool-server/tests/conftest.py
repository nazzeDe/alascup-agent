from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "unit: 单元测试（单模块内部逻辑）")
    config.addinivalue_line("markers", "integration: 集成测试（服务内模块协作 + MCP 通信）")


@pytest.fixture
def config():
    from src.config import ToolServerConfig
    return ToolServerConfig()


@pytest.fixture
def cache():
    from src.cache import create_cache
    return create_cache(ttl=600)


@pytest.fixture
def sandbox(tmp_path):
    """Config with sandbox_root pointing at tmp_path."""
    from src.config import ToolServerConfig
    return ToolServerConfig(sandbox_root=str(tmp_path))
