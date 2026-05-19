from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from src.services.container import session_manager

router = APIRouter()


@router.get("/sessions")
async def list_sessions(mgr=Depends(session_manager)):
    return await mgr.list_sessions()


@router.post("/sessions", status_code=201)
async def create_session(mgr=Depends(session_manager)):
    session = await mgr.create_session()
    return {"id": str(session.id)}


@router.get("/sessions/{session_id}")
async def get_session(session_id: UUID, mgr=Depends(session_manager)):
    try:
        return await mgr.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="session not found")
