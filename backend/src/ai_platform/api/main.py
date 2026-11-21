from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ai_platform.api.errors import install_errors
from ai_platform.api.routers.chat import deletion_router
from ai_platform.api.routers.chat import router as chat_router
from ai_platform.api.routers.identity import router as identity_router
from ai_platform.api.routers.models import router as models_router
from ai_platform.clients import redis_limits
from ai_platform.db import connection
from ai_platform.settings import settings


class Health(BaseModel):
    status: Literal["ok"]


app = FastAPI(title="AI Platform", version="0.1.0")
install_errors(app)
app.include_router(identity_router)
app.include_router(models_router)
app.include_router(chat_router)
app.include_router(deletion_router)


@app.get("/api/health", operation_id="getHealth")
def health() -> Health:
    return Health(status="ok")


@app.get("/api/v1/health/live", operation_id="getLiveness")
def liveness() -> Health:
    return Health(status="ok")


@app.get("/api/v1/health/ready", operation_id="getReadiness")
def readiness() -> Health:
    url = settings().redis_url
    if not url:
        raise HTTPException(503, "Dependency unavailable")
    with connection() as conn:
        conn.execute("SELECT 1")
    if not redis_limits.ping(url):
        raise HTTPException(503, "Dependency unavailable")
    return Health(status="ok")
