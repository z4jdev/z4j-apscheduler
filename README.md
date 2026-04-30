# z4j-apscheduler

[![PyPI version](https://img.shields.io/pypi/v/z4j-apscheduler.svg)](https://pypi.org/project/z4j-apscheduler/)
[![Python](https://img.shields.io/pypi/pyversions/z4j-apscheduler.svg)](https://pypi.org/project/z4j-apscheduler/)
[![License](https://img.shields.io/pypi/l/z4j-apscheduler.svg)](https://github.com/z4jdev/z4j-apscheduler/blob/main/LICENSE)

The APScheduler adapter for [z4j](https://z4j.com).

Surfaces APScheduler jobs on the dashboard's Schedules page — read,
create, update, enable, disable, trigger, delete. Engine-agnostic:
works alongside any z4j engine adapter, or as a standalone
scheduler in projects without a queue engine.

## What it ships

| Capability | Notes |
|---|---|
| List schedules | every job APScheduler currently tracks |
| Create schedule | date / interval / cron triggers |
| Update | trigger spec, args, kwargs, paused flag |
| Enable / disable | via APScheduler's pause / resume |
| Trigger now | runs the job immediately, outside the schedule |
| Delete | clean removal from the jobstore |
| Boot inventory | full snapshot at agent connect; existing jobs show up without editing |

Supports every APScheduler jobstore: in-memory, SQLAlchemy
(Postgres / SQLite / MySQL), MongoDB, Redis.

## Install

```bash
pip install z4j-apscheduler
```

```python
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from z4j_bare import install_agent
from z4j_apscheduler import APSchedulerAdapter

scheduler = BackgroundScheduler()
scheduler.add_job(my_func, CronTrigger(minute="*/5"), id="cleanup")
scheduler.start()

install_agent(
    engines=[],  # APScheduler runs jobs in-process; no separate engine
    schedulers=[APSchedulerAdapter(scheduler=scheduler)],
    brain_url="https://brain.example.com",
    token="z4j_agent_...",
    project_id="my-project",
)
```

## Reliability

- No exception from the adapter ever propagates back into APScheduler
  or your job code.
- Jobstore writes use APScheduler's normal transactional semantics; the
  adapter only observes and surfaces, it does not rewrite the store.

## Documentation

Full docs at [z4j.dev/schedulers/apscheduler/](https://z4j.dev/schedulers/apscheduler/).

## License

Apache-2.0 — see [LICENSE](LICENSE).

## Links

- Homepage: https://z4j.com
- Documentation: https://z4j.dev
- PyPI: https://pypi.org/project/z4j-apscheduler/
- Issues: https://github.com/z4jdev/z4j-apscheduler/issues
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Security: security@z4j.com (see [SECURITY.md](SECURITY.md))
