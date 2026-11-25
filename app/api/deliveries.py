import sqlite3
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import get_resolver, get_transport
from app.api.schemas import AttemptOut, DeliveryDetail, DeliveryOut
from app.core.url_policy import Resolver
from app.db.database import get_db
from app.services import deliveries as service
from app.services.delivery import attempt_delivery

router = APIRouter(prefix="/deliveries", tags=["deliveries"])


def _detail(db: sqlite3.Connection, delivery_id: str) -> DeliveryDetail:
    row = service.get_delivery(db, delivery_id)
    if row is None:
        raise HTTPException(status_code=404, detail="delivery not found")
    attempts = [AttemptOut(**dict(a)) for a in service.attempts_for_delivery(db, delivery_id)]
    return DeliveryDetail(**dict(row), attempts=attempts)


@router.get("", response_model=list[DeliveryOut])
def list_deliveries(
    db: Annotated[sqlite3.Connection, Depends(get_db)],
    event_id: str | None = None,
    endpoint_id: str | None = None,
    status: Literal["pending", "succeeded", "failed"] | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DeliveryOut]:
    rows = service.list_deliveries(db, event_id, endpoint_id, status, limit, offset)
    return [DeliveryOut(**dict(r)) for r in rows]


@router.get("/{delivery_id}", response_model=DeliveryDetail)
def get_delivery(
    delivery_id: str, db: Annotated[sqlite3.Connection, Depends(get_db)]
) -> DeliveryDetail:
    return _detail(db, delivery_id)


@router.post(
    "/{delivery_id}/retry",
    response_model=DeliveryDetail,
    responses={409: {"description": "Retry refused: succeeded, maximum attempts, or in progress"}},
)
def retry_delivery(
    delivery_id: str,
    request: Request,
    db: Annotated[sqlite3.Connection, Depends(get_db)],
    resolver: Annotated[Resolver, Depends(get_resolver)],
    transport: Annotated[httpx.BaseTransport | None, Depends(get_transport)],
) -> DeliveryDetail:
    """Make one more attempt now. Creates a new attempt record and never exceeds the
    configured maximum. Runs synchronously, so the response shows the outcome."""
    settings = request.app.state.settings
    if service.get_delivery(db, delivery_id) is None:
        raise HTTPException(status_code=404, detail="delivery not found")
    result = attempt_delivery(
        db, settings, delivery_id, manual=True, resolver=resolver, transport=transport
    )
    if result is None:
        raise HTTPException(
            status_code=409, detail=service.conflict_reason(db, delivery_id, settings.max_attempts)
        )
    return _detail(db, delivery_id)
