"""Job submission + status + the global SSE event stream (spec §11.3, §12)."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session

from ..db import get_session
from ..jobs.bus import bus, sse_format
from ..jobs.runner import runner
from ..models import Job

router = APIRouter(prefix="/api", tags=["jobs"])


class JobOut(BaseModel):
    id: str
    project_id: str | None
    kind: str
    state: str
    steps: list
    error: str | None

    @classmethod
    def of(cls, j: Job) -> "JobOut":
        return cls(id=j.id, project_id=j.project_id, kind=j.kind, state=j.state,
                   steps=json.loads(j.steps or "[]"), error=j.error)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_session)) -> JobOut:
    j = db.get(Job, job_id)
    if not j:
        raise HTTPException(404, "job not found")
    return JobOut.of(j)


class SubmitBody(BaseModel):
    kind: str = "demo"
    project_id: str | None = None
    payload: dict = {}


@router.post("/jobs")
async def submit_job(body: SubmitBody) -> dict:
    job_id = await runner.submit(body.kind, body.project_id, body.payload)
    return {"job_id": job_id}


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    """Server-Sent Events: all job progress, previews-ready, etc."""
    async def gen():
        async for event in bus.subscribe():
            if await request.is_disconnected():
                break
            yield sse_format(event)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


@router.get("/events/ping")
async def ping() -> dict:
    """Manually emit a heartbeat onto the bus (used by the frontend smoke test)."""
    await bus.publish({"type": "ping"})
    return {"ok": True}
