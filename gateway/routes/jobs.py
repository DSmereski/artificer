"""Hive job dispatch endpoints.

Owner-side:
    POST   /v1/jobs              enqueue a new job
    GET    /v1/jobs              list recent jobs (filterable)
    GET    /v1/jobs/{id}         inspect single job

Node-side (require_node):
    GET    /v1/jobs/next         long-poll for work (30s timeout)
    POST   /v1/jobs/{id}/result  deliver a result
"""

from __future__ import annotations

import asyncio
import json as _json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from gateway.deps import rate_limited, require_device, require_node, state
from gateway.worker_pool.scheduler import NodeView


router = APIRouter(prefix="/v1/jobs", tags=["hive-jobs"])

MAX_PAYLOAD_BYTES = 65_536  # 64 KiB — generous for prompts/results, tiny for DoS


class EnqueueJobRequest(BaseModel):
    kind: str = Field(..., min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)
    required_caps: list[str] = Field(default_factory=list)
    max_attempts: int = Field(3, ge=1, le=10)

    @field_validator("payload")
    @classmethod
    def _payload_size(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(_json.dumps(v).encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ValueError(f"payload exceeds {MAX_PAYLOAD_BYTES} bytes")
        return v


class JobSummary(BaseModel):
    id: str
    kind: str
    status: str
    attempts: int
    max_attempts: int
    node_id: str | None = None
    duration_ms: int | None = None
    error: str | None = None
    created: float
    dispatched_at: float | None = None
    completed_at: float | None = None


class JobDetail(JobSummary):
    payload: dict[str, Any]
    required_caps: list[str]
    result: dict[str, Any] | None = None


def _summary(job) -> JobSummary:
    return JobSummary(
        id=job.id, kind=job.kind, status=job.status,
        attempts=job.attempts, max_attempts=job.max_attempts,
        node_id=job.node_id, duration_ms=job.duration_ms,
        error=job.error, created=job.created,
        dispatched_at=job.dispatched_at, completed_at=job.completed_at,
    )


def _detail(job) -> JobDetail:
    return JobDetail(
        **_summary(job).model_dump(),
        payload=job.payload,
        required_caps=list(job.required_caps),
        result=job.result,
    )


@router.post("", response_model=JobSummary)
def enqueue_job(
    body: EnqueueJobRequest,
    request: Request,
    device=Depends(rate_limited("writes")),
) -> JobSummary:
    st = state(request)
    job = st.dispatcher.enqueue(
        kind=body.kind,
        payload=body.payload,
        required_caps=tuple(body.required_caps),
        max_attempts=body.max_attempts,
    )
    return _summary(job)


@router.get("/next")
async def poll_next_job(
    request: Request,
    caps: str = Query("", description="Comma-separated runtime capabilities"),
    vram_mb: int = Query(0, ge=0),
    timeout: int | None = Query(
        None, ge=0, le=120,
        description="Override long-poll timeout (seconds). 0 = no wait. Max 120.",
    ),
    node=Depends(require_node),
):
    st = state(request)
    cap_set = {c.strip() for c in caps.split(",") if c.strip()}
    view = NodeView(
        node_id=node.id, caps=cap_set, vram_free_mb=int(vram_mb),
    )
    cfg_timeout = int(st.config.jobs.long_poll_timeout_s)
    deadline_s = cfg_timeout if timeout is None else max(0, int(timeout))

    poll_interval_s = 0.5
    elapsed = 0.0
    while True:
        picked = st.scheduler.pick_for_node(view)
        if picked is not None:
            return _detail(picked)
        if elapsed >= deadline_s:
            return Response(status_code=204)
        # Sleep in small slices so the request handles client disconnect
        # quickly. asyncio.sleep yields back to the event loop.
        try:
            await asyncio.sleep(poll_interval_s)
        except asyncio.CancelledError:
            return Response(status_code=204)
        elapsed += poll_interval_s


class ResultRequest(BaseModel):
    status: str = Field(..., pattern="^(done|error)$")
    output: dict[str, Any] = Field(default_factory=dict)
    error: str = Field("", max_length=1000)
    duration_ms: int = Field(0, ge=0)

    @field_validator("output")
    @classmethod
    def _output_size(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(_json.dumps(v).encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ValueError(f"output exceeds {MAX_PAYLOAD_BYTES} bytes")
        return v


class OkResponse(BaseModel):
    ok: bool


@router.post("/{job_id}/result", response_model=OkResponse)
def post_result(
    job_id: str,
    body: ResultRequest,
    request: Request,
    node=Depends(require_node),
) -> OkResponse:
    st = state(request)
    job = st.dispatcher.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.node_id != node.id:
        raise HTTPException(
            status_code=403,
            detail="job not assigned to this node",
        )
    if body.status == "done":
        ok = st.dispatcher.complete(
            job_id, result=body.output, duration_ms=body.duration_ms,
            node_id=node.id,
        )
    else:  # 'error'
        ok = st.dispatcher.report_adapter_error(
            job_id, error=body.error or "unspecified error",
            duration_ms=body.duration_ms, node_id=node.id,
        )
    if not ok:
        raise HTTPException(
            status_code=409, detail="job not in dispatched state",
        )
    return OkResponse(ok=True)


@router.get("", response_model=list[JobSummary])
def list_jobs(
    request: Request,
    kind: str | None = Query(None),
    node_id: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    device=Depends(rate_limited("vault_reads")),
) -> list[JobSummary]:
    st = state(request)
    return [
        _summary(j)
        for j in st.dispatcher.list_recent(
            limit=limit, kind=kind, node_id=node_id, status=status,
        )
    ]


@router.get("/{job_id}", response_model=JobDetail)
def get_job(
    job_id: str, request: Request, device=Depends(rate_limited("vault_reads")),
) -> JobDetail:
    st = state(request)
    job = st.dispatcher.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _detail(job)
