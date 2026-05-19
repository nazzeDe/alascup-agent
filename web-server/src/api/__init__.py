from fastapi import APIRouter

from src.api.health import router as health_router
from src.api.sessions import router as sessions_router
from src.api.chat import router as chat_router
from src.api.approval import router as approval_router
from src.api.tools import router as tools_router

api_router = APIRouter(prefix="/api")
api_router.include_router(health_router)
api_router.include_router(sessions_router)
api_router.include_router(chat_router)
api_router.include_router(approval_router)
api_router.include_router(tools_router)
