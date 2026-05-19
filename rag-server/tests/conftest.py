import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "unit: 单元测试（单模块内部逻辑）")
    config.addinivalue_line("markers", "integration: 集成测试（服务内模块协作 + MCP 通信）")


@pytest.fixture
def chroma_client_for_test():
    """In-memory ChromaDB client for testing ChromaVectorStore directly."""
    import chromadb
    return chromadb.Client()
