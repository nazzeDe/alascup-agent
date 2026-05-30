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
    return session


@router.get("/sessions/{chat_id}")
async def get_session(chat_id: UUID, mgr=Depends(session_manager)):
    try:
        return await mgr.get_session(chat_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="session not found")


@router.delete("/sessions/{chat_id}", status_code=204)
async def delete_session(chat_id: UUID, mgr=Depends(session_manager)):
    await mgr.delete_session(chat_id)
