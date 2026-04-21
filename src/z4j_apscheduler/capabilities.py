"""Capability tokens for :class:`APSchedulerAdapter`."""

from __future__ import annotations

DEFAULT_CAPABILITIES: frozenset[str] = frozenset(
    {
        "list",
        "enable",
        "disable",
        "trigger_now",
        "delete",
    },
)

__all__ = ["DEFAULT_CAPABILITIES"]
