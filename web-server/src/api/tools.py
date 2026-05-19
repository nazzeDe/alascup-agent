from fastapi import APIRouter, Depends
from src.services.container import tool_executor

router = APIRouter()


@router.get("/tools")
async def list_tools(executor=Depends(tool_executor)):
    return executor.list_tools()
