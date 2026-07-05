import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from loguru import logger

from src.api import api_router
from src.config.loader import load_rules_config, load_servers_config
from src.config.models import Settings
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


def _build_services(settings: Settings) -> Services:
    """Wire all services from a unified Settings object. Pure — no env reads, no I/O.

    Moved from module-level scattered os.environ + JSON loader calls to a single
    parameter that can be built from files (production) or inline (tests).
    """
    servers_path = (
        Path(settings.servers_config)
        if settings.servers_config
        else CONFIG_DIR / "servers.json"
    )
    servers = load_servers_config(servers_path)
    rules_config = load_rules_config(CONFIG_DIR / "rules.json")

    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required (set DATABASE_URL)")

    db = Database(settings.database_url)
    session_mgr = PostgresSessionManager(db)
    audit_logger = PostgresAuditLogger(db)
    tracer = PostgresTracer(db)

    from src.services.error_recovery import ErrorRecovery

    llm_adapter = LLMAdapter(settings.llm_config, tracer=tracer)

    registry = ServerRegistry(servers)
    tool_executor = ToolExecutor(registry)

    rule_engine = RuleEngine(rules_config)

    lifecycle = ToolCallLifecycle(session_mgr)

    return Services(
        db=db,
        llm_adapter=llm_adapter,
        session_manager=session_mgr,
        prompt_manager=PromptManager(),
        context_manager=ContextManager(
            window_size=settings.context_window_size,
            summarizer=llm_adapter.summarize,
        ),
        rule_engine=rule_engine,
        tool_executor=tool_executor,
        audit_logger=audit_logger,
        approval_bridge=ApprovalBridge(),
        error_recovery=ErrorRecovery(),
        lifecycle=lifecycle,
        agent_max_iterations=settings.agent_max_iterations,
        agent_token_ceiling_ratio=settings.agent_token_ceiling_ratio,
    )


def _configure_logging(level: str = "INFO") -> None:
    """Configure loguru: console INFO + rotating file DEBUG."""
    logger.remove()

    log_dir = Path("logs/app")
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.add(
        sys.stderr,
        level=level.upper(),
        format="{time:HH:mm:ss} | {level:<7} | {name}:{function}:{line} - {message}",
    )

    logger.add(
        str(log_dir / "{time:YYYYMMDD}.log"),
        level="DEBUG",
        rotation="00:00",
        retention="7 days",
        compression="gz",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | {name}:{function}:{line} - {message}",
    )


def create_app(
    services: Services | None = None, settings: Settings | None = None
) -> FastAPI:
    """Build a FastAPI application instance.

    Three usage modes, in order of precedence:

    1. ``create_app(services=srv)`` — tests/embedding.
       Dependencies set directly on app.state.  No lifespan, no DB connect.
       Works with ASGITransport.

    2. ``create_app(settings=s)`` — custom config, still uses lifespan.
       App runs full lifespan (logging, DB connect, MCP discovery).

    3. ``create_app()`` — production default.
       Loads Settings.from_config_dir(CONFIG_DIR) merged with env vars.
    """
    if services is not None:
        app = FastAPI(title="alascup-agent", version="0.1.0")
        app.state.services = services
        app.include_router(api_router)
        return app

    # ── Lifespan path (production / custom settings) ──────────────────
    _svc: Services | None = None
    _settings = settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal _svc
        s = _settings or Settings.from_config_dir(CONFIG_DIR)
        _configure_logging(s.log_level)
        _svc = _build_services(s)
        db = _svc.db
        await db.connect()
        await _svc.tool_executor.discover()
        app.state.services = _svc
        yield
        if db is not None:
            await db.disconnect()

    app = FastAPI(title="alascup-agent", version="0.1.0", lifespan=lifespan)
    app.include_router(api_router)
    return app


# Uvicorn entry-point: ``uvicorn src.main:app``
app = create_app()
