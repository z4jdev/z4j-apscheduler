"""The :class:`APSchedulerAdapter` - SchedulerAdapter for APScheduler.

APScheduler is the canonical engine-agnostic scheduler for Python:
it can fire bare callables, Dramatiq actors, Celery signatures,
HTTP calls. The adapter therefore doesn't advertise a specific
``engine`` - it uses whatever the job's ``kwargs`` / metadata
imply (or "apscheduler" as a neutral placeholder).

v1 surface: read + enable/disable/trigger/delete. Create/update
deferred to v1.1 same as the rq-scheduler adapter.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from z4j_core.errors import NotFoundError
from z4j_core.models import CommandResult, Schedule, ScheduleKind

from z4j_apscheduler.capabilities import DEFAULT_CAPABILITIES

logger = logging.getLogger("z4j.agent.apscheduler.scheduler")

_NAME = "apscheduler"


class APSchedulerAdapter:
    """Scheduler adapter for APScheduler.

    Args:
        scheduler: A live ``apscheduler.schedulers.base.BaseScheduler``
                   instance (``BackgroundScheduler``, ``AsyncIOScheduler``,
                   ``BlockingScheduler`` - any of them). Duck-typed
                   on ``get_jobs()`` / ``get_job(id)`` /
                   ``pause_job(id)`` / ``resume_job(id)`` /
                   ``remove_job(id)`` / ``modify_job(id, **kw)``.
        engine: The engine name to stamp on Schedule rows (defaults
                to ``"apscheduler"``). Set to ``"dramatiq"`` or
                whatever the jobs actually dispatch to when you want
                schedules to group under a specific engine on the
                Schedules page.
        project_id: Optional project id for Schedule construction.
    """

    name: str = _NAME

    def __init__(
        self,
        *,
        scheduler: Any,
        engine: str = "apscheduler",
        project_id: UUID | None = None,
    ) -> None:
        self.scheduler = scheduler
        self._engine = engine
        self._project_id = project_id or uuid4()

    # ------------------------------------------------------------------
    # Lifecycle - APScheduler has real listener events but they fire
    # on the scheduler's own loop. For a first cut we rely on periodic
    # reconciliation via list_schedules() rather than wiring per-
    # change signals into the brain.
    # ------------------------------------------------------------------

    def connect_signals(self, sink: Any) -> None:  # noqa: ARG002
        return

    def disconnect_signals(self) -> None:
        return

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def list_schedules(self) -> list[Schedule]:
        try:
            jobs = list(self.scheduler.get_jobs())
        except Exception:  # noqa: BLE001
            logger.exception("z4j apscheduler: get_jobs failed")
            return []
        out: list[Schedule] = []
        for job in jobs:
            try:
                out.append(self._to_schedule(job))
            except Exception:  # noqa: BLE001
                logger.exception(
                    "z4j apscheduler: failed to map job %r",
                    getattr(job, "id", "?"),
                )
        return out

    async def get_schedule(self, schedule_id: str) -> Schedule | None:
        try:
            job = self.scheduler.get_job(schedule_id)
        except Exception:  # noqa: BLE001
            return None
        if job is None:
            return None
        try:
            return self._to_schedule(job)
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def create_schedule(self, spec: Schedule) -> Schedule:  # noqa: ARG002
        raise NotImplementedError(
            "create_schedule is deferred to v1.1 for APScheduler.",
        )

    async def update_schedule(
        self, schedule_id: str, spec: Schedule,  # noqa: ARG002
    ) -> Schedule:
        raise NotImplementedError(
            "update_schedule is deferred to v1.1 for APScheduler.",
        )

    async def delete_schedule(self, schedule_id: str) -> CommandResult:
        try:
            self.scheduler.remove_job(schedule_id)
        except _missing_job_exception() as _exc:
            # Idempotent no-op per SchedulerAdapter contract.
            return CommandResult(
                status="success",
                result={"schedule_id": schedule_id, "noop": True},
            )
        except Exception as exc:  # noqa: BLE001
            return CommandResult(
                status="failed", error=f"remove_job failed: {exc}",
            )
        return CommandResult(
            status="success",
            result={"schedule_id": schedule_id},
        )

    async def enable_schedule(self, schedule_id: str) -> CommandResult:
        try:
            self.scheduler.resume_job(schedule_id)
        except _missing_job_exception():
            return CommandResult(
                status="failed",
                error=f"schedule {schedule_id!r} not found",
            )
        except Exception as exc:  # noqa: BLE001
            return CommandResult(status="failed", error=f"resume_job failed: {exc}")
        return CommandResult(
            status="success",
            result={"schedule_id": schedule_id, "is_enabled": True},
        )

    async def disable_schedule(self, schedule_id: str) -> CommandResult:
        try:
            self.scheduler.pause_job(schedule_id)
        except _missing_job_exception():
            return CommandResult(
                status="failed",
                error=f"schedule {schedule_id!r} not found",
            )
        except Exception as exc:  # noqa: BLE001
            return CommandResult(status="failed", error=f"pause_job failed: {exc}")
        return CommandResult(
            status="success",
            result={"schedule_id": schedule_id, "is_enabled": False},
        )

    async def trigger_now(self, schedule_id: str) -> CommandResult:
        job = None
        try:
            job = self.scheduler.get_job(schedule_id)
        except Exception:  # noqa: BLE001
            pass
        if job is None:
            raise NotFoundError(f"schedule {schedule_id!r} not found")
        try:
            self.scheduler.modify_job(
                schedule_id, next_run_time=datetime.now(UTC),
            )
        except Exception as exc:  # noqa: BLE001
            return CommandResult(status="failed", error=f"modify_job failed: {exc}")
        return CommandResult(
            status="success",
            result={"schedule_id": schedule_id, "next_run_at": "now"},
        )

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def capabilities(self) -> set[str]:
        return set(DEFAULT_CAPABILITIES)

    # ------------------------------------------------------------------
    # Internal: Job → Schedule projection
    # ------------------------------------------------------------------

    def _to_schedule(self, job: Any) -> Schedule:
        now = datetime.now(UTC)
        job_id = _safe_str(getattr(job, "id", "")) or str(uuid4())
        trigger = getattr(job, "trigger", None)
        trigger_cls = type(trigger).__name__ if trigger is not None else ""

        if trigger_cls == "CronTrigger":
            kind = ScheduleKind.CRON
            expression = _safe_str(trigger)
        elif trigger_cls == "IntervalTrigger":
            kind = ScheduleKind.INTERVAL
            expression = _safe_str(getattr(trigger, "interval_length", "0"))
        elif trigger_cls == "DateTrigger":
            kind = ScheduleKind.CLOCKED
            expression = _safe_str(getattr(trigger, "run_date", now.isoformat()))
        else:
            kind = ScheduleKind.CRON
            expression = _safe_str(trigger) or "unknown"

        is_enabled = getattr(job, "next_run_time", None) is not None

        return Schedule(
            id=_safe_uuid(job_id),
            project_id=self._project_id,
            engine=self._engine,
            scheduler=_NAME,
            name=job_id[:200],
            task_name=_safe_str(getattr(job, "func_ref", job_id))[:500],
            kind=kind,
            expression=expression[:200] or "unknown",
            timezone="UTC",
            queue=None,
            args=list(getattr(job, "args", []) or []),
            kwargs=dict(getattr(job, "kwargs", {}) or {}),
            is_enabled=is_enabled,
            next_run_at=(
                getattr(job, "next_run_time", None)
                if isinstance(getattr(job, "next_run_time", None), datetime) else None
            ),
            total_runs=0,
            external_id=job_id,
            metadata={},
            created_at=now,
            updated_at=now,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _missing_job_exception() -> tuple[type[BaseException], ...]:
    """The APScheduler exception class raised when a job id is unknown.

    Imported lazily so we don't require APScheduler to be installed
    during unit tests. When APScheduler is absent we fall through
    to the generic ``Exception`` handler.
    """
    try:
        from apscheduler.jobstores.base import (  # type: ignore[import-not-found]
            JobLookupError,
        )
    except ImportError:
        # Fallback - match a class that will never be raised, so the
        # ``except`` clause is skipped and we land in the generic
        # Exception handler (which maps to the adapter's
        # "not found" / "failed" semantics depending on the caller).
        class _Unused(Exception):
            pass
        return (_Unused,)
    return (JobLookupError,)


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:  # noqa: BLE001
        return ""


def _safe_uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except Exception:  # noqa: BLE001
        import uuid as _uuid
        return _uuid.uuid5(_uuid.NAMESPACE_OID, value)


__all__ = ["APSchedulerAdapter"]
