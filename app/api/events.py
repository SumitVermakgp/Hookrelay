import json
import sqlite3
from typing import Annotated

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, Response
from pydantic import ValidationError

from app.api.deps import get_resolver, get_transport
from app.api.schemas import DeliverySummary, EventCreate, EventOut
from app.core.url_policy import Resolver
from app.db.database import get_db
from app.services import events as service
from app.services.delivery import deliver_event

router = APIRouter(prefix="/events", tags=["events"])


async def raw_body(request: Request) -> bytes:
    """Raw request body. The size limit is enforced for all routes by middleware."""
    return await request.body()


def _to_out(db: sqlite3.Connection, event: sqlite3.Row) -> EventOut:
    return EventOut(
        id=event["id"],
        type=event["type"],
        payload=json.loads(event["payload"]),
        idempotency_key=event["idempotency_key"],
        created_at=event["created_at"],
        deliveries=[
            DeliverySummary(id=d["id"], endpoint_id=d["endpoint_id"], status=d["status"])
            for d in service.deliveries_for_event(db, event["id"])
        ],
    )


@router.post(
    "",
    response_model=EventOut,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": EventCreate.model_json_schema()}},
        }
    },
    responses={
        200: {"description": "Idempotent replay: the original event is returned"},
        409: {"description": "Idempotency-Key was used with a different request"},
        413: {"description": "Request body exceeds the size limit"},
    },
)
def create_event(
    request: Request,
    response: Response,
    background: BackgroundTasks,
    resolver: Annotated[Resolver, Depends(get_resolver)],
    transport: Annotated[httpx.BaseTransport | None, Depends(get_transport)],
    raw: Annotated[bytes, Depends(raw_body)],
    db: Annotated[sqlite3.Connection, Depends(get_db)],
    idempotency_key: Annotated[
        str | None, Header(min_length=1, max_length=255, pattern=r"^[\x21-\x7E]+$")
    ] = None,
) -> EventOut:
    try:
        body = EventCreate.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_url=False, include_input=False, include_context=False),
        ) from None
    try:
        # Stored form is canonical JSON. Delivery will sign exactly these bytes.
        payload_json = json.dumps(
            body.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    except ValueError:
        raise HTTPException(status_code=422, detail="payload is not valid JSON data") from None
    try:
        event, created = service.create_event(db, body.type, payload_json, idempotency_key)
    except service.IdempotencyConflict:
        raise HTTPException(
            status_code=409, detail="Idempotency-Key was already used with a different request"
        ) from None
    if not created:
        response.status_code = 200
        response.headers["Idempotent-Replayed"] = "true"
    elif request.app.state.settings.auto_deliver:
        # Only a newly created event is delivered; a replay never re-sends.
        background.add_task(
            deliver_event,
            request.app.state.settings,
            event["id"],
            resolver=resolver,
            transport=transport,
        )
    return _to_out(db, event)


@router.get("/{event_id}", response_model=EventOut)
def get_event(event_id: str, db: Annotated[sqlite3.Connection, Depends(get_db)]) -> EventOut:
    event = service.get_event(db, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    return _to_out(db, event)
