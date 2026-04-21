# z4j-apscheduler

[![PyPI version](https://img.shields.io/pypi/v/z4j-apscheduler.svg)](https://pypi.org/project/z4j-apscheduler/)
[![Python](https://img.shields.io/pypi/pyversions/z4j-apscheduler.svg)](https://pypi.org/project/z4j-apscheduler/)
[![License](https://img.shields.io/pypi/l/z4j-apscheduler.svg)](https://github.com/z4jdev/z4j-apscheduler/blob/main/LICENSE)


**License:** Apache 2.0
**Status:** v1.0.0 - first public release alongside `z4j-dramatiq`.

z4j scheduler-axis adapter for
[APScheduler](https://apscheduler.readthedocs.io/). APScheduler is
engine-agnostic (it can fire anything - Dramatiq actors, bare
callables, Celery signatures, HTTP calls), so this adapter pairs
with **every** engine, not just Dramatiq. The dashboard's
Schedules page uses it as the canonical non-Celery scheduler.

## Install

```bash
pip install z4j[dramatiq,apscheduler]
# or standalone:
pip install z4j-apscheduler
```

## Capabilities

| Token | Status | Note |
|---|---|---|
| `list` | ✅ | Walks `scheduler.get_jobs()` |
| `enable` / `disable` | ✅ | via `scheduler.pause_job(id)` / `resume_job(id)` |
| `trigger_now` | ✅ | `scheduler.modify_job(id, next_run_time=now)` |
| `delete` | ✅ | `scheduler.remove_job(id)` |
| `create` / `update` | ⏸️ | Deferred to v1.1 (same rationale as z4j-rqscheduler) |

## See also

- [`packages/z4j-dramatiq/`](../z4j-dramatiq/) - the most common engine pairing.
- [`docs/ADAPTER.md`](../../docs/ADAPTER.md) - generic adapter guide.

## License

Apache 2.0 - see [LICENSE](LICENSE). This package is deliberately permissively licensed so that proprietary Django / Flask / FastAPI applications can import it without any license concerns.

## Links

- Homepage: <https://z4j.com>
- Documentation: <https://z4j.dev>
- Source: <https://github.com/z4jdev/z4j-apscheduler>
- Issues: <https://github.com/z4jdev/z4j-apscheduler/issues>
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Security: `security@z4j.com` (see [SECURITY.md](SECURITY.md))
