"""Tests for :class:`APSchedulerAdapter`."""

from __future__ import annotations

import pytest

from z4j_core.models import ScheduleKind
from z4j_core.protocols import SchedulerAdapter

from z4j_apscheduler import APSchedulerAdapter
from z4j_apscheduler.capabilities import DEFAULT_CAPABILITIES


class TestProtocolConformance:
    def test_satisfies_scheduler_adapter_protocol(self, scheduler):
        adapter = APSchedulerAdapter(scheduler=scheduler)
        assert isinstance(adapter, SchedulerAdapter)

    def test_name_is_apscheduler(self, scheduler):
        adapter = APSchedulerAdapter(scheduler=scheduler)
        assert adapter.name == "apscheduler"


class TestCapabilities:
    def test_frozen_set(self):
        assert DEFAULT_CAPABILITIES == frozenset(
            {"list", "enable", "disable", "trigger_now", "delete"},
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
