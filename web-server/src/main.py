import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from loguru import logger

from src.agent.graph import build_graph
from src.api import api_router
from src.config.loader import load_llm_config, load_rules_config, load_servers_config
from src.mcp_client.executor import ToolExecutor
from src.mcp_client.registry import ServerRegistry
from src.observability.audit_logger import PostgresAuditLogger
from src.observability.tracer import PostgresTracer
from src.security.pending import ApprovalBridge
from src.security.rule_engine import RuleEngine
from src.services.context_manager import ContextManager
from src.services.db import Database
from src.services.llm_adapter import LLMAdapter
from src.services.container import Services
from src.services.tool_lifecycle import ToolCallLifecycle
from src.services.prompt_manager import PromptManager
from src.services.session_manager import PostgresSessionManager

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _build_services():
    servers_path = os.environ.get("SERVERS_CONFIG", "")
    servers_path = Path(servers_path) if servers_path else CONFIG_DIR / "servers.json"

    llm_config = load_llm_config(CONFIG_DIR / "llm.json")
    servers = load_servers_config(servers_path)
    rules_config = load_rules_config(CONFIG_DIR / "rules.json")

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required")

    db = Database(dsn)
    session_mgr = PostgresSessionManager(db)
    audit_logger = PostgresAuditLogger(db)
    tracer = PostgresTracer(db)

    from src.services.error_recovery import ErrorRecovery

    llm_adapter = LLMAdapter(llm_config, tracer=tracer)

    registry = ServerRegistry(servers)
    tool_executor = ToolExecutor(registry)

    rule_engine = RuleEngine(rules_config)

    lifecycle = ToolCallLifecycle(session_mgr)
    graph = build_graph(
        llm=llm_adapter,
        executor=tool_executor,
        rule_engine=rule_engine,
        audit_logger=audit_logger,
        lifecycle=lifecycle,
    )

    return Services(
        db=db,
        llm_adapter=llm_adapter,
        session_manager=session_mgr,
        prompt_manager=PromptManager(),
        context_manager=ContextManager(summarizer=llm_adapter.summarize),
        rule_engine=rule_engine,
        tool_executor=tool_executor,
        audit_logger=audit_logger,
        approval_bridge=ApprovalBridge(),
        graph=graph,
        error_recovery=ErrorRecovery(),
        lifecycle=lifecycle,
    )


def _configure_logging() -> None:
    """Configure loguru: console INFO + rotating file DEBUG."""
    logger.remove()

    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_dir = Path("logs/app")
    log_dir.mkdir(parents=True, exist_ok=True)

    # Console: concise format, INFO level
    logger.add(
        sys.stderr,
        level=log_level,
        format="{time:HH:mm:ss} | {level:<7} | {name}:{function}:{line} - {message}",
    )

    # File: detailed format, DEBUG level, daily rotation, 7-day retention
    logger.add(
        str(log_dir / "{time:YYYYMMDD}.log"),
        level="DEBUG",
        rotation="00:00",
        retention="7 days",
        compression="gz",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | {name}:{function}:{line} - {message}",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    services = _build_services()
    db = services.db
    await db.connect()
    await services.tool_executor.discover()
    app.state.services = services
    yield
    await db.disconnect()


app = FastAPI(title="alascup-agent", version="0.1.0", lifespan=lifespan)
app.include_router(api_router)
