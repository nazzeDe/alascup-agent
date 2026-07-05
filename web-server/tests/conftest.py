import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "unit: 单元测试（单模块内部逻辑）")
    config.addinivalue_line(
        "markers", "integration: 集成测试（服务内模块协作 + MCP 通信）"
    )
    config.addinivalue_line("markers", "asyncio: async test")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Classify tests by directory so Makefile marker filters cannot skip them."""
    for item in items:
        path = item.path
        if "unit" in path.parts and not item.get_closest_marker("unit"):
            item.add_marker(pytest.mark.unit)
        elif "integration" in path.parts and not item.get_closest_marker("integration"):
            item.add_marker(pytest.mark.integration)
