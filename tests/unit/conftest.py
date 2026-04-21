"""Shared fixtures for z4j-apscheduler unit tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pytest


@dataclass
class CronTrigger:
    """Minimal stand-in for ``apscheduler.triggers.cron.CronTrigger``."""

    expression: str = "0 3 * * *"

    def __str__(self) -> str:
        return f"cron[{self.expression}]"


@dataclass
class IntervalTrigger:
    """Minimal stand-in for ``apscheduler.triggers.interval.IntervalTrigger``."""

    interval_length: int = 60


@dataclass
class FakeJob:
    id: str
    func_ref: str = "myapp.tasks.hello"
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    trigger: Any = field(default_factory=CronTrigger)
    next_run_time: datetime | None = None


class FakeAPScheduler:
    def __init__(self) -> None:
        self._jobs: dict[str, FakeJob] = {}
        self.paused: list[str] = []
        self.resumed: list[str] = []
        self.removed: list[str] = []
        self.modified: list[tuple[str, dict[str, Any]]] = []

    # duck-typed APScheduler surface ----------------------------------

    def get_jobs(self) -> list[FakeJob]:
        return list(self._jobs.values())

    def get_job(self, job_id: str) -> FakeJob | None:
        return self._jobs.get(job_id)

    def pause_job(self, job_id: str) -> None:
        if job_id not in self._jobs:
            raise KeyError(job_id)
        self.paused.append(job_id)
        self._jobs[job_id].next_run_time = None

    def resume_job(self, job_id: str) -> None:
        if job_id not in self._jobs:
            raise KeyError(job_id)
        self.resumed.append(job_id)

    def remove_job(self, job_id: str) -> None:
        if job_id not in self._jobs:
            raise KeyError(job_id)
        self.removed.append(job_id)
        del self._jobs[job_id]

    def modify_job(self, job_id: str, **kwargs: Any) -> None:
        if job_id not in self._jobs:
            raise KeyError(job_id)
        self.modified.append((job_id, dict(kwargs)))
        for k, v in kwargs.items():
            setattr(self._jobs[job_id], k, v)

    # test helpers ----------------------------------------------------

    def register(self, job: FakeJob) -> None:
        self._jobs[job.id] = job


@pytest.fixture
def scheduler() -> FakeAPScheduler:
    s = FakeAPScheduler()
    s.register(
        FakeJob(id="job-cron-1", trigger=CronTrigger("0 3 * * *"),
                next_run_time=datetime(2026, 4, 16, 3)),
    )
    s.register(
        FakeJob(id="job-interval-1", trigger=IntervalTrigger(60),
                next_run_time=datetime(2026, 4, 15, 21)),
    )
    return s
