# Changelog

## 1.9.1 (2026-08-26)

* Carried with the coordinated fleet release. No adapter behaviour changed.

## 1.9.0 (2026-08-25)

* Correct the APScheduler dependency floor to 3.10.2. Earlier releases require the removed `pkg_resources` module at import time.

## 1.8.0 (2026-07-23)

* `trigger_now` offloads its jobstore I/O off the agent loop so a jobstore incident can no longer freeze it; a timed-out mutation is reported indeterminate.
* Part of the coordinated 1.8.0 fleet release (unified fleet version, green lint/format/import-boundary gate).

## 1.7.0 (2026-07-07)

* Capability table corrected to match the adapter's real support.
* Python 3.11 is now the minimum supported version (3.10 dropped).
* Part of the coordinated 1.7.0 fleet release (unified fleet version, green lint/format/import-boundary gate).

## 1.4.0 (2026-05-02)

Initial 1.4.0 release: APScheduler companion. Observation-only adapter that surfaces APScheduler jobs in the dashboard.
