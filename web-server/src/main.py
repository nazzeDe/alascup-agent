import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from src.agent.graph import build_graph
from src.api import api_router
from src.config.loader import load_llm_config, load_rules_config, load_servers_config
from src.mcp_client.executor import ToolExecutor
from src.mcp_client.registry import ServerRegistry
from src.observability.audit_logger import PostgresAuditLogger
from src.observability.tracer import PostgresTracer
from src.persistence.checkpoint import PostgresCheckpointer
from src.security.pending import ApprovalBridge
from src.security.rule_engine import RuleEngine
from src.services.context_manager import ContextManager
from src.services.db import Database
from src.services.llm_adapter import LLMAdapter
from src.services.container import Services
from src.services.prompt_manager import PromptManager
from src.services.session_manager import PostgresSessionManager

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _build_services():
    llm_config = load_llm_config(CONFIG_DIR / "llm.json")
    servers = load_servers_config(CONFIG_DIR / "servers.json")
    rules_config = load_rules_config(CONFIG_DIR / "rules.json")

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required")

    db = Database(dsn)
    session_mgr = PostgresSessionManager(db)
    audit_logger = PostgresAuditLogger(db)
    tracer = PostgresTracer(db)
    checkpointer = PostgresCheckpointer(db)

    llm_adapter = LLMAdapter(llm_config, tracer=tracer)

    registry = ServerRegistry(servers)
    tool_executor = ToolExecutor(registry)

    rule_engine = RuleEngine(rules_config)
    graph = build_graph(
        llm=llm_adapter,
        executor=tool_executor,
        rule_engine=rule_engine,
        audit_logger=audit_logger,
        checkpointer=checkpointer,
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
        checkpointer=checkpointer,
        graph=graph,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    services = _build_services()
    db = services.db
    await db.connect()
    app.state.services = services
    yield
    await db.disconnect()


app = FastAPI(title="alascup-agent", version="0.1.0", lifespan=lifespan)
app.include_router(api_router)
