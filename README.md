# z4j-apscheduler

[![PyPI version](https://img.shields.io/pypi/v/z4j-apscheduler.svg)](https://pypi.org/project/z4j-apscheduler/)
[![Python](https://img.shields.io/pypi/pyversions/z4j-apscheduler.svg)](https://pypi.org/project/z4j-apscheduler/)
[![License](https://img.shields.io/pypi/l/z4j-apscheduler.svg)](https://github.com/z4jdev/z4j-apscheduler/blob/main/LICENSE)

The APScheduler adapter for [z4j](https://z4j.com).

Surfaces APScheduler jobs on the dashboard's Schedules page, read,
enable, disable, trigger, delete. Engine-agnostic:
works alongside any z4j engine adapter, or as a standalone
scheduler in projects without a queue engine.

## Compatibility

- APScheduler 3.10.2+ and <4 (capped below the APScheduler 4.x rewrite)
- Python 3.11+

Full per-adapter matrix at <https://z4j.dev/reference/compatibility/>.

## What it ships

| Capability | Notes |
|---|---|
| List schedules | every job APScheduler currently tracks |
| Enable / disable | via APScheduler's pause / resume |
| Trigger now | sets the existing job's `next_run_time` to now; subsequent timing follows APScheduler's normal trigger semantics |
| Delete | clean removal from the jobstore |
| Boot inventory | full snapshot at agent connect; existing jobs show up without editing |

Create and update are not yet supported: the adapter surfaces and
controls the jobs you define in your own APScheduler setup, and the
brain greys out create / update for this scheduler.

Supports every APScheduler jobstore: in-memory, SQLAlchemy
(Postgres / SQLite / MySQL), MongoDB, Redis.

## Install

```bash
pip install z4j-bare z4j-apscheduler
```

```python
import os

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
    hmac_secret=os.environ["Z4J_HMAC_SECRET"],
)
```

## Reliability

- Inventory and listener failures are isolated from APScheduler job code.
- Operator controls call APScheduler's normal pause, resume, remove, and
  `modify_job` APIs and therefore mutate the configured jobstore. A control
  that exceeds its 10-second timeout is reported as indeterminate because its
  worker thread may still complete the mutation.

## Documentation

Full docs at [z4j.dev/schedulers/apscheduler/](https://z4j.dev/schedulers/apscheduler/).

## License

Apache-2.0, see [LICENSE](LICENSE).

## Links

- Homepage: https://z4j.com
- Documentation: https://z4j.dev
- PyPI: https://pypi.org/project/z4j-apscheduler/
- Issues: https://github.com/z4jdev/z4j-apscheduler/issues
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Security: security@z4j.com (see [SECURITY.md](SECURITY.md))
