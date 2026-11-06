# Data Studio v0.38 — Modular Application Core

Data Studio is a Python-native desktop platform for data preparation, analytics, dashboards, statistics, engineering calculations, machine learning, automation, governance, deployment observability, and rollback management.

## What v0.38 changes

v0.38 begins the architecture roadmap without removing v0.37 functionality. It introduces independent modules for:

- versioned project-state defaults, validation, and migration;
- structured diagnostics with correlation IDs and optional JSONL persistence;
- shared scheduling, dependency, and retry policies;
- background tasks with progress, cancellation, and bounded worker pools;
- pure governance and calculation logic retained from v0.37.

The existing desktop and headless entry points now consume these modules. Legacy v0.36 and v0.37 projects are upgraded in memory to v0.38 and retain migration history.

## Run

Use `run_data_studio_v0.38.bat`, or install `requirements_v0.38.txt` and run `python data_studio_v0.38.py`.

Run the release gate with:

```text
python data_studio_ci_v0.38.py --project .
```

Open only trusted `.pdw` and `.pkl` files; legacy compatibility still relies on Python pickle.
