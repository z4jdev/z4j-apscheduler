"""The :class:`APSchedulerAdapter` - SchedulerAdapter for APScheduler.

APScheduler is the canonical engine-agnostic scheduler for Python:
it can fire bare callables, Dramatiq actors, Celery signatures,
HTTP calls. The adapter therefore doesn't advertise a specific
``engine`` - it uses whatever the job's ``kwargs`` / metadata
imply (or "apscheduler" as a neutral placeholder).

The adapter supports read, enable, disable, trigger, and delete. It does not
advertise create or update.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from z4j_core.errors import NotFoundError
from z4j_core.models import CommandResult, Schedule, ScheduleKind

from z4j_apscheduler._offload import (
    OffloadTimeoutError,
    indeterminate_timeout_result,
    offload,
)
from z4j_apscheduler.capabilities import DEFAULT_CAPABILITIES

logger = logging.getLogger("z4j.adapter.apscheduler.scheduler")

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
        # H10: the wire identity for a schedule action is the schedule's NAME /
        # external_id, both capped at 200 chars (Schedule field max_length). An
        # APScheduler job id longer than that is truncated for the snapshot, so
        # a later trigger/enable/disable/delete would call ``get_job`` with the
        # TRUNCATED id and miss the real (>200-char) job. We keep the truncation
        # (an untruncated id fails Schedule validation and false-DELETES the
        # row), and instead remember ``truncated key -> full job id`` from the
        # last list_schedules so the target methods can resolve back to the full
        # id. Bounded by the live job count; rebuilt on every snapshot.
        self._full_job_id_by_key: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Lifecycle - APScheduler has real listener events but they fire
    # on the scheduler's own loop. For a first cut we rely on periodic
    # reconciliation via list_schedules() rather than wiring per-
    # change signals into the brain.
    # ------------------------------------------------------------------

    def connect_signals(self, sink: Any) -> None:
        return

    def disconnect_signals(self) -> None:
        return

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def list_schedules(self) -> list[Schedule]:
        # RM6: get_jobs() queries the (possibly persistent) jobstore
        # synchronously; offload it so a slow SQL/redis/mongo store cannot
        # freeze the agent loop during boot / periodic snapshots. A timeout or
        # store error PROPAGATES rather than returning [] -- the runtime's
        # snapshot emitter treats an empty list as an AUTHORITATIVE inventory
        # and would delete every schedule, whereas a raise makes it skip the
        # snapshot (see _emit_schedule_snapshot, and RM5). Only per-job mapping
        # errors are tolerated below.
        jobs = await offload(self.scheduler.get_jobs, timeout=10.0)
        # H10 + H9 + M9: rebuild the wire-identity -> full-id resolution map from
        # this snapshot so the target methods can address a job whose id was
        # truncated / disambiguated for the wire. The key is ``_wire_identity``,
        # the SAME injective, whitespace/round-trip-stable value used for the
        # Schedule's name + external_id below -- so the id the brain sends back
        # (after pydantic's strip + 200-char cap) resolves exactly, and two ids
        # sharing a 200-char prefix (or differing only in surrounding whitespace)
        # never collapse to one addressable schedule.
        key_map: dict[str, str] = {}
        for job in jobs:
            raw = _raw_job_id(job)  # the ORIGINAL id (surrogates/NUL intact)
            if not raw:
                continue
            wire = _wire_identity(raw)
            if wire != raw:
                # Map the wire identity the brain echoes back to the RAW id
                # get_job/remove_job/etc. actually need.
                key_map[wire] = raw
        self._full_job_id_by_key = key_map
        out: list[Schedule] = []
        for job in jobs:
            try:
                out.append(self._to_schedule(job))
            except Exception:
                # apscheduler:95: do NOT silently omit a job that failed to map --
                # an omission reads as a deletion to the brain's reconciler and
                # removes a schedule that still exists. Preserve it as a minimal
                # placeholder so the id-set stays complete; only drop it if it has
                # no usable id (nothing to preserve).
                logger.exception(
                    "z4j apscheduler: failed to map job %r; emitting a placeholder "
                    "to keep it in the inventory",
                    getattr(job, "id", "?"),
                )
                placeholder = self._placeholder_schedule(job)
                if placeholder is not None:
                    out.append(placeholder)
        return out

    def _placeholder_schedule(self, job: Any) -> Schedule | None:
        """A minimal, always-valid Schedule for a job whose full mapping raised,
        so it stays REPRESENTED in the inventory snapshot (apscheduler:95).
        Returns None only when the job has no usable id (nothing to preserve)."""
        raw_id = _raw_job_id(job)
        if not raw_id:
            return None
        wire = _wire_identity(raw_id)  # injective + strip-stable + non-empty
        now = datetime.now(UTC)
        try:
            return Schedule(
                id=_safe_uuid(wire),
                project_id=self._project_id,
                engine=self._engine,
                scheduler=_NAME,
                name=wire,
                # RM4: task_name must survive pydantic's strip. job_id[:500] could
                # strip to empty (a whitespace-only id) and re-drop the very job
                # this placeholder exists to keep. _wire_identity is guaranteed
                # non-empty and strip-stable.
                task_name=wire[:500],
                kind=ScheduleKind.CRON,
                expression="unknown",
                timezone="UTC",
                queue=None,
                args=[],
                kwargs={},
                is_enabled=getattr(job, "next_run_time", None) is not None,
                next_run_at=None,
                total_runs=0,
                # H9/M9/RH8: injective, round-trip-stable wire identity (matches
                # name + the resolver key) so even a placeholder for a >200-char /
                # whitespace / surrogate id stays uniquely addressable.
                external_id=wire,
                metadata={"z4j_mapping": "degraded"},
                created_at=now,
                updated_at=now,
            )
        except Exception:
            return None

    def _resolve_job_id(self, schedule_id: str) -> str:
        """Map a wire schedule_id back to the full APScheduler job id (H10).

        The brain addresses a schedule by its (<=200-char) name / external_id;
        for a job whose real id was truncated, resolve it via the last
        snapshot's key map. Unknown keys pass through unchanged, so ordinary
        (<=200-char) ids -- which equal their own key -- are unaffected.
        """
        return self._full_job_id_by_key.get(schedule_id, schedule_id)

    async def _resolve_job_id_async(self, schedule_id: str) -> str:
        """:meth:`_resolve_job_id`, refreshing the map once if it cannot resolve.

        The key map is only ever populated by a successful inventory, so a
        command that arrives BEFORE the first one -- an agent restarted while a
        command was in flight, or a schedule addressed immediately after the
        agent came up -- could not resolve a sanitized or truncated id and
        addressed a job that does not exist. Refresh once on a miss, then answer
        from the refreshed map. Ordinary ids resolve on the fast path and never
        reach the refresh, and a genuinely unknown id still passes through
        unchanged after one attempt, so this cannot loop.
        """
        # Fast-path on actual MEMBERSHIP, not on the cache being non-empty.
        # ``or self._full_job_id_by_key`` suppressed the refresh whenever the map
        # held any entry at all, so a job added since the last inventory could
        # not be addressed until some other inventory happened to run.
        if schedule_id in self._full_job_id_by_key:
            return self._full_job_id_by_key[schedule_id]
        try:
            await self.list_schedules()
        except Exception:
            # A failed inventory is not this call's problem to report; fall back
            # to the pass-through, which is what it did before.
            return self._resolve_job_id(schedule_id)
        return self._resolve_job_id(schedule_id)

    async def get_schedule(self, schedule_id: str) -> Schedule | None:
        # RM6 (sibling): get_job() is the same synchronous jobstore I/O as
        # get_jobs()/remove_job() and every other method here offloads it;
        # leaving this one inline reintroduced the loop-freeze on a slow store.
        # A single-item lookup is not a snapshot, so a timeout maps to "not
        # found" (None) rather than propagating.
        schedule_id = await self._resolve_job_id_async(schedule_id)
        try:
            job = await offload(self.scheduler.get_job, schedule_id, timeout=10.0)
        except Exception:
            return None
        if job is None:
            return None
        try:
            return self._to_schedule(job)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def create_schedule(self, spec: Schedule) -> Schedule:
        raise NotImplementedError(
            "create_schedule is not supported by the APScheduler adapter.",
        )

    async def update_schedule(
        self,
        schedule_id: str,
        spec: Schedule,
    ) -> Schedule:
        raise NotImplementedError(
            "update_schedule is not supported by the APScheduler adapter.",
        )

    async def delete_schedule(self, schedule_id: str) -> CommandResult:
        # M4: remove_job hits the (possibly persistent) jobstore synchronously;
        # offload it so a slow jobstore cannot freeze the agent loop.
        # Resolve to the RAW native id for the local APS API only; keep the
        # caller's wire ``schedule_id`` for the CommandResult / error text. The
        # native id may carry surrogates / NUL / be >1 MiB, which would make the
        # RESULT frame unserializable (DROP-classified / force-purged) AFTER the
        # action already landed. The wire id already traversed the wire, so it is
        # safe to echo back and is the identity the caller knows.
        native_id = await self._resolve_job_id_async(schedule_id)
        try:
            await offload(self.scheduler.remove_job, native_id, timeout=10.0)
        except _missing_job_exception():
            # Idempotent no-op per SchedulerAdapter contract.
            return CommandResult(
                status="success",
                result={"schedule_id": schedule_id, "noop": True},
            )
        except OffloadTimeoutError:
            return indeterminate_timeout_result(
                "delete_schedule", 10.0, hint="the job may still have been removed"
            )
        except Exception as exc:
            return CommandResult(
                status="failed",
                error=f"remove_job failed: {_safe_str(exc)[:500]}",
            )
        return CommandResult(
            status="success",
            result={"schedule_id": schedule_id},
        )

    async def enable_schedule(self, schedule_id: str) -> CommandResult:
        # Native id for the local API, wire id for the result/error.
        native_id = await self._resolve_job_id_async(schedule_id)
        try:
            await offload(self.scheduler.resume_job, native_id, timeout=10.0)
        except _missing_job_exception():
            return CommandResult(
                status="failed",
                error=f"schedule {schedule_id!r} not found",
            )
        except OffloadTimeoutError:
            return indeterminate_timeout_result(
                "enable_schedule", 10.0, hint="the resume may still have landed"
            )
        except Exception as exc:
            return CommandResult(
                status="failed", error=f"resume_job failed: {_safe_str(exc)[:500]}"
            )
        return CommandResult(
            status="success",
            result={"schedule_id": schedule_id, "is_enabled": True},
        )

    async def disable_schedule(self, schedule_id: str) -> CommandResult:
        # Native id for the local API, wire id for the result/error.
        native_id = await self._resolve_job_id_async(schedule_id)
        try:
            await offload(self.scheduler.pause_job, native_id, timeout=10.0)
        except _missing_job_exception():
            return CommandResult(
                status="failed",
                error=f"schedule {schedule_id!r} not found",
            )
        except OffloadTimeoutError:
            return indeterminate_timeout_result(
                "disable_schedule", 10.0, hint="the pause may still have landed"
            )
        except Exception as exc:
            return CommandResult(status="failed", error=f"pause_job failed: {_safe_str(exc)[:500]}")
        return CommandResult(
            status="success",
            result={"schedule_id": schedule_id, "is_enabled": False},
        )

    async def trigger_now(self, schedule_id: str) -> CommandResult:
        # get_job / modify_job hit the APScheduler jobstore, which for a
        # persistent backend (SQLAlchemy / redis / mongo) is synchronous
        # I/O. Run them on the dedicated broker-offload pool under a timeout
        # so a slow jobstore cannot freeze the agent event loop OR starve its
        # heartbeat providers (isolated from the default executor).
        # Native id for the local API, wire id for the result/error.
        native_id = await self._resolve_job_id_async(schedule_id)
        job = None
        with contextlib.suppress(Exception):
            job = await offload(self.scheduler.get_job, native_id, timeout=10.0)
        if job is None:
            raise NotFoundError(f"schedule {schedule_id!r} not found")
        try:
            await offload(
                self.scheduler.modify_job,
                native_id,
                next_run_time=datetime.now(UTC),
                timeout=10.0,
            )
        except OffloadTimeoutError:
            # modify_job may still have landed on the jobstore; report
            # indeterminate rather than a clean failure.
            return indeterminate_timeout_result(
                "trigger_now",
                10.0,
                hint="the job may still have been enqueued",
            )
        except Exception as exc:
            return CommandResult(
                status="failed", error=f"modify_job failed: {_safe_str(exc)[:500]}"
            )
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
        raw_id = _raw_job_id(job) or str(uuid4())
        # The injective, round-trip-stable identity used for id, name,
        # and external_id, so a >200-char / whitespace / surrogate id can never
        # collapse two live jobs into one addressable schedule.
        wire = _wire_identity(raw_id)
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

        # M13: keep args/kwargs JSON-serializable so the runtime's authoritative
        # snapshot (model_dump(mode="json")) cannot blow up on a bytes / custom
        # object arg and drop this job -- which the brain would read as a
        # deletion. A degrade marks the schedule so the omission is visible.
        args_val, kwargs_val, degraded = _json_safe_args_kwargs(
            list(getattr(job, "args", []) or []),
            dict(getattr(job, "kwargs", {}) or {}),
        )

        _func = _safe_str(getattr(job, "func_ref", None))
        return Schedule(
            id=_safe_uuid(wire),
            project_id=self._project_id,
            engine=self._engine,
            scheduler=_NAME,
            # H9/M9/RH8: name + external_id + id all use the injective wire
            # identity so a >200-char / prefix-colliding / whitespace / surrogate
            # id stays uniquely addressable and never collapses two live jobs.
            name=wire,
            # apscheduler:95 + RM4: a lambda / unpicklable job has func_ref=None;
            # an empty task_name fails Schedule validation and drops the job. Fall
            # back to the wire identity (guaranteed non-empty and strip-stable) --
            # ``func_ref or wire`` was wrong because a whitespace-only func_ref /
            # id strips to empty in pydantic and re-drops the job.
            task_name=(_func if _func.strip() else wire)[:500],
            kind=kind,
            expression=expression[:200] or "unknown",
            timezone="UTC",
            queue=None,
            args=args_val,
            kwargs=kwargs_val,
            is_enabled=is_enabled,
            next_run_at=(
                getattr(job, "next_run_time", None)
                if isinstance(getattr(job, "next_run_time", None), datetime)
                else None
            ),
            total_runs=0,
            external_id=wire,
            metadata={"z4j_mapping": "degraded_args"} if degraded else {},
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
        class _UnusedError(Exception):
            pass

        return (_UnusedError,)
    return (JobLookupError,)


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    try:
        s = str(value)
    except Exception:
        return ""
    # M10: scrub lone surrogates (e.g. os.fsdecode(b"\xff") -> "\udcff"). str()
    # keeps them, but a lone surrogate is NOT encodable to UTF-8 for the wire
    # snapshot; left in a schedule field it makes the WHOLE authoritative
    # snapshot fail to serialize and get silently dropped, so the brain
    # reconciler false-deletes the schedule. backslashreplace renders each
    # surrogate as a printable, injective escape and is a no-op for clean text.
    s = s.encode("utf-8", "backslashreplace").decode("utf-8")
    # RM5: also scrub embedded NUL. It UTF-8 encodes fine but PostgreSQL text /
    # JSONB REJECT it, so a NUL in any schedule field sends the whole snapshot
    # batch down brain's permanent-error ingest path (dropped + acked).
    return s.replace("\x00", "\\x00")


#: A reserved marker that a raw job id can produce only by being forced
#: down the disambiguation branch. Because a "wire-clean" id is returned only
#: when this marker is ABSENT, the clean-output namespace and the disambiguated
#: output namespace ("<base>~~z4j~~<sha256>") are provably disjoint -- no clean
#: id can ever equal another id's disambiguated form.
_WIRE_SENTINEL = "~~z4j~~"


def _raw_job_id(job: Any) -> str:
    """The ORIGINAL job id string (surrogates / NUL intact) for hashing + lookup."""
    v = getattr(job, "id", "")
    return v if isinstance(v, str) else _safe_str(v)


def _wire_identity(raw_id: str) -> str:
    """An INJECTIVE, wire-stable identity for an APScheduler job id.

    The Schedule model caps name/external_id at 200 chars and strips surrounding
    whitespace, so the id the brain echoes back is ``raw_id[:200].strip()`` --
    NOT injective on its own (two ids sharing a 200-char prefix, or differing
    only by surrounding whitespace or by a lone surrogate scrubbed to the same
    escape, collapse to one addressable schedule) and may not round-trip.

    Return the id UNCHANGED only when it is already wire-safe AND wire-clean:
    scrubbing changed nothing, no surrounding whitespace, <=200 chars, and it
    does not itself contain :data:`_WIRE_SENTINEL`. Otherwise return a
    deterministic ``<base>~~z4j~~<sha256-of-RAW-bytes>`` (H9 / M9 /):
    - the sentinel makes clean and disambiguated outputs disjoint, so a clean id
      that merely LOOKS like a disambiguated one is forced to its OWN digest and
      cannot collide with a long id;
    - hashing the RAW surrogatepass bytes (not the scrubbed string) keeps two
      distinct lone-surrogate ids distinct;
    - the result is <=200 chars with no surrounding whitespace, so it round-trips
      through pydantic's strip + cap unchanged.
    """
    if not isinstance(raw_id, str):
        raw_id = _safe_str(raw_id)
    scrubbed = _safe_str(raw_id)
    stripped = scrubbed.strip()
    if (
        scrubbed == raw_id
        and scrubbed == stripped
        and len(scrubbed) <= 200
        and _WIRE_SENTINEL not in scrubbed
    ):
        return scrubbed
    digest = hashlib.sha256(
        raw_id.encode("utf-8", "surrogatepass"), usedforsecurity=False
    ).hexdigest()
    base = stripped[: 200 - len(_WIRE_SENTINEL) - len(digest)].rstrip()
    return f"{base}{_WIRE_SENTINEL}{digest}"


def _has_nul(obj: Any) -> bool:
    """True if a NUL char appears anywhere in a str / dict keys+values / seq."""
    if isinstance(obj, str):
        return "\x00" in obj
    if isinstance(obj, dict):
        return any(_has_nul(k) or _has_nul(v) for k, v in obj.items())
    if isinstance(obj, (list, tuple)):
        return any(_has_nul(x) for x in obj)
    return False


def _json_safe_args_kwargs(
    args: list[Any],
    kwargs: dict[Any, Any],
) -> tuple[list[Any], dict[str, Any], bool]:
    """Coerce a job's args/kwargs to JSON-serializable values.

    M13: an APScheduler job can carry ARBITRARY Python objects (bytes, a
    custom class instance) as args/kwargs. ``_to_schedule`` maps them verbatim
    and does NOT raise -- but the agent runtime later serializes the
    AUTHORITATIVE schedule snapshot with ``model_dump(mode="json")``, and a
    non-serializable value blows up THERE. The old degraded-mapping fallback
    only covered ``_to_schedule`` raising, so this slipped through: the job was
    dropped from the snapshot and the brain reconciler deleted its row (a false
    deletion). Serialize-test here and, on failure, degrade to a safe ``str``
    representation so the job stays REPRESENTED (with a degraded marker) instead
    of vanishing. Returns ``(args, kwargs, degraded)``.
    """

    # A value must degrade when it would not survive the WIRE serialization:
    # - M10: a lone surrogate passes json.dumps but raises UnicodeEncodeError at
    #   UTF-8 encode time -- exactly where the runtime's serialization fails.
    # - RL2: allow_nan=False makes NaN / +-Inf raise ValueError (json otherwise
    #   emits the non-standard NaN/Infinity tokens that strict JSON / JSONB null).
    # - RM5: an embedded NUL UTF-8 encodes fine but PostgreSQL text/JSONB REJECT
    #   it, so check _has_nul explicitly. On any of these the value degrades to a
    #   scrubbed _safe_str (which strips surrogate + NUL) WITH the degraded marker,
    #   instead of silently stalling / nulling in the authoritative snapshot.
    def _wire_unsafe(obj: Any) -> bool:
        try:
            json.dumps(obj, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError):
            return True
        return _has_nul(obj)

    degraded = False
    if _wire_unsafe(args):
        args = [_safe_str(a) for a in args]
        degraded = True
    safe_kwargs: dict[str, Any] = kwargs
    if _wire_unsafe(kwargs):
        safe_kwargs = {_safe_str(k): _safe_str(v) for k, v in kwargs.items()}
        degraded = True
    return args, safe_kwargs, degraded


def _safe_uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except Exception:
        import uuid as _uuid

        return _uuid.uuid5(_uuid.NAMESPACE_OID, value)


__all__ = ["APSchedulerAdapter"]
