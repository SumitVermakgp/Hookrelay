import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db.database import get_db

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    database: str


@router.get("/health", response_model=HealthResponse)
def health(db: Annotated[sqlite3.Connection, Depends(get_db)]) -> HealthResponse:
    try:
        db.execute("SELECT 1").fetchone()
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail="database unavailable") from None
    return HealthResponse(status="ok", database="ok")
