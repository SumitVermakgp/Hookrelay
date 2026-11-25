from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class EndpointCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)
    description: str | None = Field(default=None, max_length=200)


class EndpointPublic(BaseModel):
    """Shape for list responses. Has no secret field by design."""

    id: str
    url: str
    description: str | None
    created_at: datetime


class EndpointCreated(EndpointPublic):
    """Returned once, at creation. The only response that carries the secret."""

    secret: str


class EventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}$")
    payload: dict[str, Any]


class DeliverySummary(BaseModel):
    id: str
    endpoint_id: str
    status: Literal["pending", "succeeded", "failed"]


class EventOut(BaseModel):
    id: str
    type: str
    payload: dict[str, Any]
    idempotency_key: str | None
    created_at: datetime
    deliveries: list[DeliverySummary]


class DeliveryOut(BaseModel):
    id: str
    event_id: str
    endpoint_id: str
    status: Literal["pending", "succeeded", "failed"]
    attempt_count: int
    created_at: datetime
    next_attempt_at: datetime | None


class AttemptOut(BaseModel):
    attempt_number: int
    status_code: int | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class DeliveryDetail(DeliveryOut):
    attempts: list[AttemptOut]
