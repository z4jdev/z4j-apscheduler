"""z4j-apscheduler - APScheduler adapter for z4j's Schedules UI.

Public API:

- :class:`APSchedulerAdapter` - pass to ``install_agent(schedulers=[...])``.

Licensed under Apache License 2.0.
"""

from __future__ import annotations

from z4j_apscheduler.scheduler import APSchedulerAdapter

try:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("z4j-apscheduler")
except PackageNotFoundError:  # source checkout, no installed metadata
    from z4j_core.version import __version__  # type: ignore[no-redef]

__all__ = ["APSchedulerAdapter", "__version__"]
