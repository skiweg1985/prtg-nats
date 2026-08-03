"""Version 1 of the API."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routes import (
    audit,
    auth,
    credentials,
    deployments,
    enrollment,
    iperf,
    jobs,
    probes,
    sensors,
    system,
    users,
)

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth.router)
api_router.include_router(system.router)
api_router.include_router(probes.router)
api_router.include_router(enrollment.router)
api_router.include_router(sensors.router)
api_router.include_router(deployments.router)
api_router.include_router(jobs.router)
api_router.include_router(iperf.router)
api_router.include_router(credentials.router)
api_router.include_router(audit.router)
api_router.include_router(users.router)
