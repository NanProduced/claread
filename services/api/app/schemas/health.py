"""Health Check API Schemas."""

from __future__ import annotations

from pydantic import BaseModel


class HealthCheckResponse(BaseModel):
    status: str
    app: str
    env: str
    postgres: bool
    redis: bool
    worker: bool
    worker_inflight_tasks: int
    dict_cache: DictCacheStats | None = None
    zilliz: bool | None = None


class DictCacheStats(BaseModel):
    l1_size: int
    l1_max_size: int
    l1_hits: int
    l1_misses: int
    l2_hits: int
    l2_misses: int


class DbHealthResponse(BaseModel):
    status: str
    postgres: bool


class ReadinessCheckResponse(BaseModel):
    status: str
    postgres: bool
    worker: bool
    worker_inflight_tasks: int
