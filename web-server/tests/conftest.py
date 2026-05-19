import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "unit: 单元测试（单模块内部逻辑）")
    config.addinivalue_line("markers", "integration: 集成测试（服务内模块协作 + MCP 通信）")
    config.addinivalue_line("markers", "asyncio: async test")
