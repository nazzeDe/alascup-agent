from fastapi import APIRouter, Depends

from src.services.container import db as db_dep, tool_executor
from src.observability.timing import get_tracker

router = APIRouter()


@router.get("/health")
async def health(
    db=Depends(db_dep),
    executor=Depends(tool_executor),
):
    checks: dict[str, str] = {}

    # PostgreSQL connectivity
    try:
        await db.fetchrow("SELECT 1")
        checks["postgres"] = "ok"
    except Exception:
        checks["postgres"] = "error"

    # Tool discovery
    tools = executor.list_tools()
    checks["tools_discovered"] = str(len(tools))

    status = "ok" if checks.get("postgres") == "ok" else "degraded"
    return {"status": status, "checks": checks, "version": "0.1.0"}


@router.get("/metrics")
async def metrics(executor=Depends(tool_executor)):
    tracker = get_tracker()
    summary = tracker.summarize_feature_durations()
    return {
        "tools_discovered": len(executor.list_tools()),
        "feature_durations_avg_ms": summary,
    }
