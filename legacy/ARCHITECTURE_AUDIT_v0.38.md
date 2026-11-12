# Data Studio v0.38 Architecture Audit

## Result

v0.38 creates the first stable module boundaries around the remaining desktop monolith. State migration, diagnostics, job policy, task execution, and calculation/governance logic no longer need to be defined inside the main window class.

## New boundaries

| Module | Responsibility |
|---|---|
| `data_studio_core_v0_38.py` | Governance calculations, validation rules, safe expressions, health scoring |
| `data_studio_state_v0_38.py` | Project defaults, migration, schema validation |
| `data_studio_diagnostics_v0_38.py` | Structured events and JSONL evidence |
| `data_studio_jobs_v0_38.py` | Scheduling, dependencies, retries |
| `data_studio_tasks_v0_38.py` | Background task lifecycle, progress, cancellation |
| `data_studio_headless_v0.38.py` | CLI orchestration and atomic persistence |
| `data_studio_v0.38.py` | Desktop composition and user workflows |

## Improvements delivered

- Project loading is no longer an unvalidated direct assignment process.
- Desktop and headless paths share migration and scheduling policies.
- Diagnostics can be correlated across start and finish events.
- Headless runs create append-only diagnostic evidence beside the project.
- Background work has a reusable foundation that does not depend on PySide6.

## Remaining debt

The desktop window still contains most workflow dialogs and business operations. v0.39 should move persistence away from unrestricted pickle and add a signed manifest-based package. Later releases should extract data, pipeline, model, dashboard, monitoring, and deployment services and wire long operations to the task manager.
