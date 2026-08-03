"""Response shapes shared across the API.

Pydantic models, never ORM objects: what the browser sees is decided here and
cannot change because somebody added a column.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class Page(ApiModel, Generic[T]):
    items: list[T]
    total: int | None = None
    # Cursor pagination: the id to pass as `before` for the next page. Absent
    # when this is the last one.
    next_cursor: str | None = None


class ActionRef(ApiModel):
    """An action the caller may perform on this object right now.

    Derived server-side from permissions and locks, so the interface can enable
    a button without duplicating either rule.
    """

    name: str
    permitted: bool
    # Set when the action exists but is currently impossible, e.g. a running
    # job holds the probe. The value is a translation key.
    blocked_reason_key: str | None = None


class JobAccepted(ApiModel):
    """The 202 body of every action endpoint."""

    job_id: str
    status: str
    # Where to watch it: the interface opens this SSE stream immediately.
    events_url: str


class ErrorBody(ApiModel):
    code: str
    message_key: str
    params: dict[str, Any] = Field(default_factory=dict)
    fields: list[str] = Field(default_factory=list)
    details: str | None = None
    correlation_id: str | None = None
    retryable: bool = False


class ErrorResponse(ApiModel):
    error: ErrorBody


class HealthResponse(ApiModel):
    status: str
    version: str


class ReadinessCheck(ApiModel):
    name: str
    ok: bool
    detail: str | None = None


class ReadinessResponse(ApiModel):
    ready: bool
    checks: list[ReadinessCheck]


class TimestampedModel(ApiModel):
    created_at: datetime
    updated_at: datetime
