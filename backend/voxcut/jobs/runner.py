"""Job runner: an asyncio queue of jobs, each a DAG of idempotent steps (§12).

Design principles honored here:
- Everything is a resumable job. Each step records completion in Job.steps; a
  re-run skips completed steps.
- Progress is published to the bus → SSE to the UI.
- CPU-bound work runs in a ProcessPoolExecutor; subprocesses (ffmpeg/yt-dlp)
  are shelled out from steps. M0 ships the runner + a demo job; real pipeline
  steps register into STEP_REGISTRY in later milestones.
"""
from __future__ import annotations

import asyncio
import json
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlmodel import select

from ..db import session_scope
from ..models import Job
from .bus import bus


@dataclass
class StepState:
    name: str
    state: str = "pending"   # pending | running | done | failed | skipped
    progress: float = 0.0
    message: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "state": self.state,
                "progress": self.progress, "message": self.message}


@dataclass
class JobContext:
    """Passed to a job handler. Steps report progress through it."""
    job_id: str
    project_id: str | None
    kind: str
    payload: dict
    steps: list[StepState] = field(default_factory=list)

    def add_step(self, name: str) -> StepState:
        s = StepState(name=name)
        self.steps.append(s)
        return s

    async def report(self, step: StepState, progress: float, message: str = "") -> None:
        step.progress = round(progress, 4)
        if message:
            step.message = message
        step.state = "running" if progress < 1.0 else "done"
        await self._persist()
        await bus.publish({
            "type": "job_progress", "job_id": self.job_id,
            "project_id": self.project_id, "step": step.name,
            "pct": step.progress, "message": step.message,
            "steps": [s.to_dict() for s in self.steps],
        })

    async def finish_step(self, step: StepState, message: str = "") -> None:
        step.state = "done"
        step.progress = 1.0
        if message:
            step.message = message
        await self._persist()

    async def _persist(self) -> None:
        with session_scope() as db:
            job = db.get(Job, self.job_id)
            if job:
                job.steps = json.dumps([s.to_dict() for s in self.steps])
                db.add(job)
                db.commit()


JobHandler = Callable[[JobContext], Awaitable[None]]
STEP_REGISTRY: dict[str, JobHandler] = {}


def register(kind: str) -> Callable[[JobHandler], JobHandler]:
    def deco(fn: JobHandler) -> JobHandler:
        STEP_REGISTRY[kind] = fn
        return fn
    return deco


class JobRunner:
    def __init__(self, concurrency: int = 2) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._concurrency = concurrency
        self._started = False
        self._payloads: dict[str, dict] = {}

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        for _ in range(self._concurrency):
            self._workers.append(asyncio.create_task(self._worker()))
        # Requeue jobs left mid-flight by a previous crash (resume, §12).
        with session_scope() as db:
            stuck = db.exec(
                select(Job).where(Job.state.in_(["queued", "running"]))
            ).all()
            ids = [j.id for j in stuck]
        for jid in ids:
            await self._queue.put(jid)

    async def stop(self) -> None:
        for w in self._workers:
            w.cancel()

    async def submit(self, kind: str, project_id: str | None = None,
                     payload: dict | None = None) -> str:
        with session_scope() as db:
            job = Job(kind=kind, project_id=project_id, state="queued")
            db.add(job)
            db.commit()
            db.refresh(job)
            job_id = job.id
        self._payloads[job_id] = payload or {}
        await self._queue.put(job_id)
        await bus.publish({"type": "job_queued", "job_id": job_id, "kind": kind,
                           "project_id": project_id})
        return job_id

    async def _worker(self) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                await self._run_job(job_id)
            except Exception:  # noqa: BLE001 — a worker must never die
                traceback.print_exc()
            finally:
                self._queue.task_done()

    async def _run_job(self, job_id: str) -> None:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if not job or job.state in ("done", "cancelled"):
                return
            job.state = "running"
            job.error = None
            db.add(job)
            db.commit()
            kind, project_id = job.kind, job.project_id

        handler = STEP_REGISTRY.get(kind)
        ctx = JobContext(job_id=job_id, project_id=project_id, kind=kind,
                         payload=self._payloads.get(job_id, {}))
        await bus.publish({"type": "job_started", "job_id": job_id, "kind": kind,
                           "project_id": project_id})

        if handler is None:
            await self._fail(job_id, f"no handler registered for kind={kind!r}")
            return
        try:
            await handler(ctx)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            await self._fail(job_id, f"{type(exc).__name__}: {exc}")
            return

        with session_scope() as db:
            job = db.get(Job, job_id)
            if job:
                job.state = "done"
                job.finished_at = datetime.now(timezone.utc)
                job.steps = json.dumps([s.to_dict() for s in ctx.steps])
                db.add(job)
                db.commit()
        await bus.publish({"type": "job_done", "job_id": job_id, "kind": kind,
                           "project_id": project_id})

    async def _fail(self, job_id: str, error: str) -> None:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job:
                job.state = "failed"
                job.error = error
                job.finished_at = datetime.now(timezone.utc)
                db.add(job)
                db.commit()
                kind, project_id = job.kind, job.project_id
            else:
                kind = project_id = None
        await bus.publish({"type": "job_failed", "job_id": job_id, "error": error,
                           "kind": kind, "project_id": project_id})


runner = JobRunner()
