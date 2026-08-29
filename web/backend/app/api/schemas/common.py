"""Response shapes shared across the API.

Pydantic models, never ORM objects: what the browser sees is decided here and
cannot change because somebody added a column.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class JobAccepted(ApiModel):
    """The 202 body of every action endpoint."""

    job_id: str
    status: str
    # Where to watch it: the interface opens this SSE stream immediately.
    events_url: str


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
