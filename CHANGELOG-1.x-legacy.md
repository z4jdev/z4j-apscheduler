# Changelog

All notable changes to `z4j-apscheduler` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.1] - 2026-04-21

### Changed

- Lowered minimum Python version from 3.13 to 3.11. This package now supports Python 3.11, 3.12, 3.13, and 3.14.
- Documentation polish: standardized on ASCII hyphens across README, CHANGELOG, and docstrings for consistent rendering on PyPI.


## [1.0.0] - 2026-04

### Added

- First public release.
- `APSchedulerAdapter` implementing `z4j_core.protocols.SchedulerAdapter` against any `apscheduler.BaseScheduler` (`BackgroundScheduler`, `AsyncIOScheduler`, `BlockingScheduler`).
- Read-side capabilities: `list` (walks `scheduler.get_jobs()`), `read`.
- Lifecycle capabilities: `enable` (`resume_job`), `disable` (`pause_job`), `trigger_now` (`modify_job(next_run_time=now)`), `delete` (`remove_job`).
- Engine-agnostic by design - pairs with Dramatiq, RQ, Celery, bare callables, or any custom executor.
- `capabilities.py` declares the supported action set so the dashboard renders only valid buttons.

### Deferred

- `create` / `update` capabilities - APScheduler's job-spec API requires more design work to expose safely from a remote dashboard. Tracked for v1.1.

## Links

- Repository: <https://github.com/z4jdev/z4j-apscheduler>
- Issues: <https://github.com/z4jdev/z4j-apscheduler/issues>
- PyPI: <https://pypi.org/project/z4j-apscheduler/>

[Unreleased]: https://github.com/z4jdev/z4j-apscheduler/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/z4jdev/z4j-apscheduler/releases/tag/v1.0.0
