"""Bounded, session-local background batches; no external queue or persistence."""

from __future__ import annotations

import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field
from datetime import datetime
from threading import Event, RLock
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from .server import AgentTasker


@dataclass
class _Batch:
    id: str
    concurrency: int
    results: Dict[str, Dict[str, Any]]
    status: str = "running"
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None
    finished: Optional[float] = None
    error: Optional[str] = None
    cancel: Event = field(default_factory=Event)
    future: Optional[Future] = None


class BatchManager:
    """Coordinate outside task workers so background batches cannot deadlock them."""

    def __init__(self, tasker: AgentTasker, *, max_batches: int, ttl_seconds: int):
        if max_batches < 1 or ttl_seconds < 1:
            raise ValueError("Background batch limit and TTL must be positive integers")
        self.tasker = tasker
        self.max_batches = max_batches
        self.ttl_seconds = ttl_seconds
        self._jobs: Dict[str, _Batch] = {}
        self._lock = RLock()
        self._closed = False
        self._executor = ThreadPoolExecutor(max_workers=max_batches, thread_name_prefix="tasker-batch")

    def _expire(self) -> None:
        now = time.monotonic()
        for batch_id, job in list(self._jobs.items()):
            if job.finished is not None and now - job.finished >= self.ttl_seconds:
                del self._jobs[batch_id]

    def _lookup(self, batch_id: str) -> _Batch:
        self._expire()
        job = self._jobs.get(batch_id)
        if job is None:
            raise ValueError(f"Unknown or expired batch_id: {batch_id}")
        return job

    @staticmethod
    def _snapshot(job: _Batch) -> Dict[str, Any]:
        results = list(job.results.values())  # Dict insertion order matches input tasks.
        counts = {status: sum(item["status"] == status for item in results)
                  for status in ("completed", "failed", "cancelled", "running", "queued")}
        snapshot = {
            "batch_id": job.id,
            "status": job.status,
            "concurrency": job.concurrency,
            "total": len(results),
            "completed": counts["completed"],
            "failed": counts["failed"],
            "cancelled": counts["cancelled"],
            "running": counts["running"],
            "pending": counts["queued"],
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "results": results,
        }
        if job.error is not None:
            snapshot["error"] = job.error
        return snapshot

    def submit(self, definitions: list, *, concurrency: int) -> Dict[str, Any]:
        # Validation is synchronous: reject bad graphs/payloads before creating a job.
        concurrency = self.tasker.validate_concurrency(concurrency)
        prepared = self.tasker._prepare_tasks(definitions)
        with self._lock:
            if self._closed:
                raise RuntimeError("Server is closing")
            self._expire()
            if len(self._jobs) >= self.max_batches:
                finished = [job for job in self._jobs.values() if job.finished is not None]
                if not finished:
                    raise RuntimeError(f"Background batch limit reached ({self.max_batches}); collect or cancel active batches")
                oldest = min(finished, key=lambda job: job.finished)
                del self._jobs[oldest.id]
            job = _Batch(
                id=uuid.uuid4().hex,
                concurrency=concurrency,
                results={task.name: {"id": task.id, "name": task.name, "task_type": task.task_type.value,
                                     "status": "queued", "result": None, "error": None} for task in prepared},
            )
            self._jobs[job.id] = job
            job.future = self._executor.submit(self._run, job, prepared)
            return self._snapshot(job)

    def _run(self, job: _Batch, prepared: list) -> None:
        def update(result: Dict[str, Any]) -> None:
            with self._lock:
                job.results[result["name"]] = result

        raw = None
        error = None
        try:
            raw = self.tasker._execute_prepared(
                prepared, concurrency=job.concurrency, cancel=job.cancel, on_update=update,
            )
        except Exception as exc:
            error = str(exc)
        finally:
            with self._lock:
                if raw is not None:
                    job.results = {result["name"]: result for result in raw["results"]}
                    job.status = "cancelled" if raw.get("cancelled") else "failed" if raw["failed"] else "completed"
                else:
                    job.status = "failed"
                    job.error = error
                job.completed_at = datetime.now().isoformat()
                job.finished = time.monotonic()

    def get(self, batch_id: str, *, wait_seconds: int = 0, cancel: Optional[Event] = None) -> Dict[str, Any]:
        with self._lock:
            job = self._lookup(batch_id)
            future = job.future
        deadline = time.monotonic() + wait_seconds
        while future is not None and not future.done() and time.monotonic() < deadline and not (cancel and cancel.is_set()):
            try:
                future.result(timeout=min(0.1, max(0, deadline - time.monotonic())))
            except TimeoutError:
                pass
        with self._lock:
            return self._snapshot(job)

    def cancel(self, batch_id: str) -> Dict[str, Any]:
        with self._lock:
            job = self._lookup(batch_id)
            if job.finished is None:
                job.cancel.set()
                job.status = "cancelling"
            return self._snapshot(job)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for job in self._jobs.values():
                if job.finished is None:
                    job.cancel.set()
        # Drain coordinators before the single execution pool is shut down.
        self._executor.shutdown(wait=True)
