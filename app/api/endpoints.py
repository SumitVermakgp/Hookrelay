import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.api.deps import get_resolver
from app.api.schemas import EndpointCreate, EndpointCreated, EndpointPublic
from app.core.url_policy import Resolver, UrlPolicyError, validate_destination_url
from app.db.database import get_db
from app.services import endpoints as service

router = APIRouter(prefix="/endpoints", tags=["endpoints"])


@router.post(
    "",
    response_model=EndpointCreated,
    status_code=201,
    responses={409: {"description": "The url is already registered, or the limit is reached"}},
)
def register_endpoint(
    body: EndpointCreate,
    request: Request,
    response: Response,
    db: Annotated[sqlite3.Connection, Depends(get_db)],
    resolver: Annotated[Resolver, Depends(get_resolver)],
) -> EndpointCreated:
    settings = request.app.state.settings
    try:
        validate_destination_url(body.url, settings.test_allowed_hosts, resolver)
    except UrlPolicyError as exc:
        raise HTTPException(status_code=422, detail=f"url rejected: {exc}") from None
    try:
        row = service.create_endpoint(db, body.url, body.description, settings.max_endpoints)
    except service.EndpointExists:
        raise HTTPException(status_code=409, detail="url is already registered") from None
    except service.EndpointLimitReached:
        raise HTTPException(
            status_code=409,
            detail=f"endpoint limit reached ({settings.max_endpoints})",
        ) from None
    response.headers["Cache-Control"] = "no-store"
    return EndpointCreated(**dict(row))


@router.get("", response_model=list[EndpointPublic])
def list_endpoints(
    db: Annotated[sqlite3.Connection, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[EndpointPublic]:
    return [EndpointPublic(**dict(r)) for r in service.list_endpoints(db, limit, offset)]
