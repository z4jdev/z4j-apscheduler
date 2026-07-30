"""Tests for :class:`APSchedulerAdapter`."""

from __future__ import annotations

import pytest
from z4j_apscheduler import APSchedulerAdapter
from z4j_apscheduler.capabilities import DEFAULT_CAPABILITIES
from z4j_core.models import ScheduleKind
from z4j_core.protocols import SchedulerAdapter


class TestProtocolConformance:
    def test_satisfies_scheduler_adapter_protocol(self, scheduler):
        adapter = APSchedulerAdapter(scheduler=scheduler)
        assert isinstance(adapter, SchedulerAdapter)

    def test_name_is_apscheduler(self, scheduler):
        adapter = APSchedulerAdapter(scheduler=scheduler)
        assert adapter.name == "apscheduler"


class TestCapabilities:
    def test_frozen_set(self):
        assert (
            frozenset(
                {"list", "enable", "disable", "trigger_now", "delete"},
            )
            == DEFAULT_CAPABILITIES
        )


class TestList:
    @pytest.mark.asyncio
    async def test_lists_all_jobs(self, scheduler):
        adapter = APSchedulerAdapter(scheduler=scheduler)
        items = await adapter.list_schedules()
        assert len(items) == 2

    @pytest.mark.asyncio
    async def test_cron_kind_detected(self, scheduler):
        adapter = APSchedulerAdapter(scheduler=scheduler)
        items = await adapter.list_schedules()
        cron = next(s for s in items if s.name == "job-cron-1")
        assert cron.kind == ScheduleKind.CRON

    @pytest.mark.asyncio
    async def test_interval_kind_detected(self, scheduler):
        adapter = APSchedulerAdapter(scheduler=scheduler)
        items = await adapter.list_schedules()
        inter = next(s for s in items if s.name == "job-interval-1")
        assert inter.kind == ScheduleKind.INTERVAL

    @pytest.mark.asyncio
    async def test_lambda_func_ref_none_still_lists(self, scheduler):
        # apscheduler:95: a lambda / unpicklable job has func_ref=None. task_name
        # falls back to the stable job id and the job maps FULLY (real cron
        # expression, NOT degraded). Asserting the FULL map -- not just a
        # non-empty name -- is what distinguishes the fallback from the degraded
        # placeholder rescue (a reverted fallback would map via the placeholder,
        # which also has a matching name + non-empty task_name).
        from tests.unit.conftest import FakeJob

        scheduler.register(FakeJob(id="job-lambda", func_ref=None))
        adapter = APSchedulerAdapter(scheduler=scheduler)
        items = await adapter.list_schedules()
        lam = next(s for s in items if s.name == "job-lambda")
        assert lam.task_name == "job-lambda"  # the id fallback
        assert lam.expression != "unknown"  # real cron trigger, NOT the placeholder
        assert (lam.metadata or {}).get("z4j_mapping") != "degraded"

    @pytest.mark.asyncio
    async def test_long_job_id_still_mapped_not_dropped(self, scheduler):
        # A >200-char job id is disambiguated to an injective <=200-char wire
        # identity (H9/M9), so it still lists (never dropped as a false delete).
        from z4j_apscheduler.scheduler import _wire_identity

        from tests.unit.conftest import FakeJob

        long_id = "j" * 300
        scheduler.register(FakeJob(id=long_id))
        adapter = APSchedulerAdapter(scheduler=scheduler)
        items = await adapter.list_schedules()
        wire = _wire_identity(long_id)
        assert len(wire) <= 200
        assert any(s.name == wire for s in items)  # mapped, not dropped

    @pytest.mark.asyncio
    async def test_long_job_id_targetable_via_wire_identity(self, scheduler):
        # H9/H10: the wire addresses a schedule by its <=200-char name/external_id
        # (the injective _wire_identity). A >200-char job id must still be
        # reachable by trigger/enable/disable/delete -- list_schedules builds a
        # wire-identity -> full-id map the target methods resolve through.
        from z4j_apscheduler.scheduler import _wire_identity

        from tests.unit.conftest import FakeJob

        long_id = "z" * 300
        scheduler.register(FakeJob(id=long_id, next_run_time=None))
        adapter = APSchedulerAdapter(scheduler=scheduler)
        await adapter.list_schedules()  # populate the resolution map

        key = _wire_identity(long_id)  # what the brain sends back as schedule_id
        # disable -> pause_job must land on the FULL id, not the wire key.
        res = await adapter.disable_schedule(key)
        assert res.status == "success"
        assert long_id in scheduler.paused

        # delete -> remove_job must also hit the full id (not a no-op miss).
        res = await adapter.delete_schedule(key)
        assert res.status == "success"
        assert long_id in scheduler.removed
        assert res.result and res.result.get("noop") is not True

    @pytest.mark.asyncio
    async def test_prefix_colliding_ids_stay_distinct_h9(self, scheduler):
        # H9: two ids sharing the first 200 chars must NOT collapse to one
        # addressable schedule. Distinct wire identities + distinct external_ids.
        from z4j_apscheduler.scheduler import _wire_identity

        from tests.unit.conftest import FakeJob

        prefix = "p" * 250
        id_a = prefix + "AAA"
        id_b = prefix + "BBB"
        scheduler.register(FakeJob(id=id_a, next_run_time=None))
        scheduler.register(FakeJob(id=id_b, next_run_time=None))
        adapter = APSchedulerAdapter(scheduler=scheduler)
        items = await adapter.list_schedules()
        externals = {s.external_id for s in items}
        assert _wire_identity(id_a) in externals
        assert _wire_identity(id_b) in externals
        assert _wire_identity(id_a) != _wire_identity(id_b)
        # Each resolves to its OWN full id, not a shared "winner".
        await adapter.disable_schedule(_wire_identity(id_a))
        await adapter.disable_schedule(_wire_identity(id_b))
        assert id_a in scheduler.paused
        assert id_b in scheduler.paused

    @pytest.mark.asyncio
    async def test_normal_job_id_passes_through_resolver(self, scheduler):
        # The resolver must be inert for ordinary (<=200-char) ids: an id absent
        # from the key map resolves to itself, so existing targeting is unchanged
        # even without a preceding list_schedules.
        adapter = APSchedulerAdapter(scheduler=scheduler)
        res = await adapter.disable_schedule("job-cron-1")
        assert res.status == "success"
        assert "job-cron-1" in scheduler.paused

    def test_placeholder_carries_degraded_marker_over_model_dump(self, scheduler):
        # apscheduler:123 cross-package contract: the brain preserves config iff
        # data["metadata"]["z4j_mapping"] == "degraded". This asserts the marker
        # survives the REAL _placeholder_schedule -> model_dump(mode="json") the
        # agent ships, so a rename/serialization drift of the marker fails here
        # rather than silently restoring the config-clobber.
        from tests.unit.conftest import FakeJob

        adapter = APSchedulerAdapter(scheduler=scheduler)
        ph = adapter._placeholder_schedule(FakeJob(id="degraded-1"))
        assert ph is not None
        dumped = ph.model_dump(mode="json")
        assert dumped["metadata"]["z4j_mapping"] == "degraded"

    @pytest.mark.asyncio
    async def test_unmappable_job_emits_placeholder(self, scheduler, monkeypatch):
        # apscheduler:95: if a job cannot be mapped AT ALL, it is preserved as a
        # placeholder rather than omitted, so the reconciler never deletes a row
        # for a schedule that still exists.
        from tests.unit.conftest import FakeJob

        scheduler.register(FakeJob(id="job-bad"))
        adapter = APSchedulerAdapter(scheduler=scheduler)
        real_to_schedule = adapter._to_schedule

        def _boom_for_bad(job):
            if getattr(job, "id", None) == "job-bad":
                raise ValueError("cannot map this job")
            return real_to_schedule(job)

        monkeypatch.setattr(adapter, "_to_schedule", _boom_for_bad)
        items = await adapter.list_schedules()
        names = {s.name for s in items}
        assert "job-bad" in names  # preserved via placeholder, NOT omitted
        assert len(items) == 3  # the two good jobs + the placeholder

    @pytest.mark.asyncio
    async def test_non_json_args_degraded_not_dropped(self, scheduler):
        # M13: a job whose args/kwargs carry a non-JSON-serializable value
        # (bytes here) maps cleanly in _to_schedule but would blow up LATER in
        # the runtime's model_dump(mode="json"), dropping the job from the
        # authoritative snapshot -> brain deletes its row. The adapter now
        # coerces args/kwargs to JSON-safe values and marks the schedule
        # degraded, and the whole Schedule must survive model_dump(mode="json").
        import json

        from tests.unit.conftest import FakeJob

        scheduler.register(
            FakeJob(
                id="job-bytes",
                args=(b"\xff\xfe", 1),
                kwargs={"blob": b"\x00", "ok": "yes"},
            ),
        )
        adapter = APSchedulerAdapter(scheduler=scheduler)
        items = await adapter.list_schedules()
        job = next(s for s in items if s.name == "job-bytes")
        assert (job.metadata or {}).get("z4j_mapping") == "degraded_args"
        # The load-bearing assertion: the snapshot serialization the runtime
        # runs must NOT raise for this job.
        dumped = job.model_dump(mode="json")
        json.dumps(dumped)  # must not raise
        assert dumped["kwargs"]["ok"] == "yes"

    @pytest.mark.asyncio
    async def test_lone_surrogate_arg_degraded_snapshot_survives_m10(self, scheduler):
        # M10: a lone-surrogate value (os.fsdecode(b"\xff") == "\udcff") PASSES
        # json.dumps but raises UnicodeEncodeError at UTF-8 encode time -- exactly
        # where the runtime's wire serialization fails, silently stalling the whole
        # snapshot. The adapter now detects it via the wire encoder and scrubs it,
        # so the schedule degrades (not drops) and the dump is UTF-8 encodable.
        import json

        from tests.unit.conftest import FakeJob

        # A lone surrogate (what os.fsdecode(b"\xff") yields under
        # surrogateescape); constructed directly so the test is platform-portable.
        surrogate = "\udcff"
        scheduler.register(
            FakeJob(id="job-surrogate", args=(surrogate,), kwargs={"k": surrogate}),
        )
        adapter = APSchedulerAdapter(scheduler=scheduler)
        items = await adapter.list_schedules()
        job = next(s for s in items if s.name == "job-surrogate")
        assert (job.metadata or {}).get("z4j_mapping") == "degraded_args"
        dumped = job.model_dump(mode="json")
        # The load-bearing assertion: UTF-8 encoding (the wire step) must not raise.
        json.dumps(dumped, ensure_ascii=False).encode("utf-8")

    @pytest.mark.asyncio
    async def test_json_safe_args_not_degraded(self, scheduler):
        # The degrade path must be inert for ordinary JSON-safe jobs: no marker,
        # values preserved verbatim.
        from tests.unit.conftest import FakeJob

        scheduler.register(
            FakeJob(id="job-plain", args=("a", 2), kwargs={"k": "v"}),
        )
        adapter = APSchedulerAdapter(scheduler=scheduler)
        items = await adapter.list_schedules()
        job = next(s for s in items if s.name == "job-plain")
        assert (job.metadata or {}).get("z4j_mapping") != "degraded_args"
        assert job.args == ["a", 2]
        assert job.kwargs == {"k": "v"}

    @pytest.mark.asyncio
    async def test_engine_override_stamps_engine_name(self, scheduler):
        adapter = APSchedulerAdapter(scheduler=scheduler, engine="dramatiq")
        items = await adapter.list_schedules()
        assert all(s.engine == "dramatiq" for s in items)

    @pytest.mark.asyncio
    async def test_default_engine_is_apscheduler(self, scheduler):
        adapter = APSchedulerAdapter(scheduler=scheduler)
        items = await adapter.list_schedules()
        assert all(s.engine == "apscheduler" for s in items)

    @pytest.mark.asyncio
    async def test_is_enabled_reflects_next_run_time(self, scheduler):
        # Pause one job - its next_run_time becomes None.
        scheduler.pause_job("job-cron-1")
        adapter = APSchedulerAdapter(scheduler=scheduler)
        items = await adapter.list_schedules()
        lookup = {s.name: s.is_enabled for s in items}
        assert lookup["job-cron-1"] is False
        assert lookup["job-interval-1"] is True


class TestDelete:
    @pytest.mark.asyncio
    async def test_removes_job(self, scheduler):
        adapter = APSchedulerAdapter(scheduler=scheduler)
        result = await adapter.delete_schedule("job-cron-1")
        assert result.status == "success"
        assert "job-cron-1" in scheduler.removed


class TestEnableDisable:
    @pytest.mark.asyncio
    async def test_disable_pauses(self, scheduler):
        adapter = APSchedulerAdapter(scheduler=scheduler)
        result = await adapter.disable_schedule("job-cron-1")
        assert result.status == "success"
        assert "job-cron-1" in scheduler.paused

    @pytest.mark.asyncio
    async def test_enable_resumes(self, scheduler):
        adapter = APSchedulerAdapter(scheduler=scheduler)
        result = await adapter.enable_schedule("job-cron-1")
        assert result.status == "success"
        assert "job-cron-1" in scheduler.resumed

    @pytest.mark.asyncio
    async def test_missing_id_fails_on_disable(self, scheduler):
        adapter = APSchedulerAdapter(scheduler=scheduler)
        result = await adapter.disable_schedule("ghost")
        assert result.status == "failed"


class TestTriggerNow:
    @pytest.mark.asyncio
    async def test_sets_next_run_to_now(self, scheduler):
        adapter = APSchedulerAdapter(scheduler=scheduler)
        result = await adapter.trigger_now("job-cron-1")
        assert result.status == "success"
        # modify_job was called with next_run_time
        mod_ids = [jid for jid, _ in scheduler.modified]
        assert "job-cron-1" in mod_ids


class TestWireIdSafetyR8M7:
    """Action RESULTS carry the caller's WIRE id (already wire-safe), not
    the resolved NATIVE id (which may hold surrogates / NUL / be >1 MiB and make
    the result frame unserializable AFTER the action already landed). The native
    id is used only for the local APScheduler API call."""

    @pytest.mark.asyncio
    async def test_delete_result_echoes_wire_id(self, scheduler):
        adapter = APSchedulerAdapter(scheduler=scheduler)
        # A wire key that resolves to a DIFFERENT native job id.
        adapter._full_job_id_by_key = {"wire-key": "job-cron-1"}
        result = await adapter.delete_schedule("wire-key")
        assert result.status == "success"
        assert "job-cron-1" in scheduler.removed  # native id used for the API
        assert result.result["schedule_id"] == "wire-key"  # wire id echoed

    @pytest.mark.asyncio
    async def test_trigger_result_echoes_wire_id(self, scheduler):
        adapter = APSchedulerAdapter(scheduler=scheduler)
        adapter._full_job_id_by_key = {"wire-key": "job-cron-1"}
        result = await adapter.trigger_now("wire-key")
        assert result.status == "success"
        assert result.result["schedule_id"] == "wire-key"
        mod_ids = [jid for jid, _ in scheduler.modified]
        assert "job-cron-1" in mod_ids  # native id used for the API

    @pytest.mark.asyncio
    async def test_missing_id_raises_notfound(self, scheduler):
        from z4j_core.errors import NotFoundError

        adapter = APSchedulerAdapter(scheduler=scheduler)
        with pytest.raises(NotFoundError):
            await adapter.trigger_now("ghost")


class TestCreateUpdateDeferred:
    @pytest.mark.asyncio
    async def test_create_raises_notimplemented(self, scheduler):
        adapter = APSchedulerAdapter(scheduler=scheduler)
        with pytest.raises(NotImplementedError):
            await adapter.create_schedule(None)  # type: ignore[arg-type]


class TestWireIdentityInjectivityR6:
    """_wire_identity must be genuinely injective + round-trip-stable."""

    def test_clean_id_equal_to_a_disambiguated_form_is_forced_to_own_digest(self):
        # Collision (1): a short raw id that LITERALLY equals some long id's
        # disambiguated "<base>~~z4j~~<hash>" form must NOT collapse onto it.
        from z4j_apscheduler.scheduler import _WIRE_SENTINEL, _wire_identity

        long_id = "L" * 201
        wire_long = _wire_identity(long_id)
        assert _WIRE_SENTINEL in wire_long
        assert len(wire_long) <= 200
        # A different job whose raw id IS that disambiguated string:
        wire_of_that = _wire_identity(wire_long)
        assert wire_of_that != wire_long  # forced down the hash branch -> distinct

    def test_lone_surrogate_ids_stay_distinct(self):
        # Collision (2): two distinct lone-surrogate ids must not scrub to the
        # same wire identity (the hash is over the RAW surrogatepass bytes).
        from z4j_apscheduler.scheduler import _wire_identity

        assert _wire_identity("\udcff") != _wire_identity("\udcfe")

    def test_prefix_collision_stays_distinct(self):
        from z4j_apscheduler.scheduler import _wire_identity

        pre = "p" * 250
        a, b = _wire_identity(pre + "A"), _wire_identity(pre + "B")
        assert a != b and len(a) <= 200 and len(b) <= 200

    def test_clean_id_unchanged(self):
        from z4j_apscheduler.scheduler import _wire_identity

        assert _wire_identity("myapp.tasks.hello") == "myapp.tasks.hello"


class TestApsWireHardeningR6:
    @pytest.mark.asyncio
    async def test_whitespace_only_id_lambda_still_lists_rm4(self, scheduler):
        # RM4: a whitespace-only id with func_ref=None must NOT be dropped -- both
        # the normal and placeholder task_name fallbacks must survive strip.
        from tests.unit.conftest import FakeJob

        scheduler.register(FakeJob(id="   ", func_ref=None))
        adapter = APSchedulerAdapter(scheduler=scheduler)
        items = await adapter.list_schedules()
        # The whitespace job is represented (some schedule with a non-empty,
        # strip-stable task_name + external_id).
        assert all(s.task_name.strip() for s in items)
        assert all((s.external_id or "").strip() for s in items)
        assert len(items) == 3  # the two fixtures + the whitespace job

    @pytest.mark.asyncio
    async def test_embedded_nul_arg_degrades_and_serializes_rm5(self, scheduler):
        import json

        from tests.unit.conftest import FakeJob

        scheduler.register(FakeJob(id="job-nul", args=("a\x00b",), kwargs={"k": "x\x00y"}))
        adapter = APSchedulerAdapter(scheduler=scheduler)
        items = await adapter.list_schedules()
        job = next(s for s in items if s.name == "job-nul")
        assert (job.metadata or {}).get("z4j_mapping") == "degraded_args"
        dumped = job.model_dump(mode="json")
        # No NUL survives into the wire payload (PostgreSQL text/JSONB reject it).
        assert "\x00" not in json.dumps(dumped)

    @pytest.mark.asyncio
    async def test_embedded_nul_in_id_scrubbed_rm5(self, scheduler):
        import json

        from tests.unit.conftest import FakeJob

        scheduler.register(FakeJob(id="job\x00id", next_run_time=None))
        adapter = APSchedulerAdapter(scheduler=scheduler)
        items = await adapter.list_schedules()
        # The NUL id maps (not dropped) and no field carries a raw NUL.
        assert any("job" in (s.external_id or "") for s in items)
        for s in items:
            assert "\x00" not in json.dumps(s.model_dump(mode="json"))

    @pytest.mark.asyncio
    async def test_non_finite_float_arg_degrades_rl2(self, scheduler):
        import json
        import math

        from tests.unit.conftest import FakeJob

        scheduler.register(
            FakeJob(id="job-nan", args=(math.nan, math.inf), kwargs={"k": -math.inf})
        )
        adapter = APSchedulerAdapter(scheduler=scheduler)
        items = await adapter.list_schedules()
        job = next(s for s in items if s.name == "job-nan")
        assert (job.metadata or {}).get("z4j_mapping") == "degraded_args"
        # Strict JSON (allow_nan=False) accepts the degraded value (no NaN/Inf).
        json.dumps(job.model_dump(mode="json"), allow_nan=False)
