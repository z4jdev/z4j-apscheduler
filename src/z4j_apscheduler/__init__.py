"""z4j-apscheduler - APScheduler adapter for z4j's Schedules UI.

Public API:

- :class:`APSchedulerAdapter` - pass to ``install_agent(schedulers=[...])``.

Licensed under Apache License 2.0.
"""

from __future__ import annotations

from z4j_apscheduler.scheduler import APSchedulerAdapter

__version__ = "1.4.0"

__all__ = ["APSchedulerAdapter", "__version__"]
