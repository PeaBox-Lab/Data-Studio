# Data Studio v0.38 Release Notes

## Modular Application Core

- Added a project-state module containing canonical defaults, structural validation, and explicit migration to v0.38.
- Added migration support for v0.36 and v0.37 projects, including active-dataset repair and environment-state normalization.
- Added structured diagnostic events with UTC timestamps, correlation IDs, severity, component, action, message, and context.
- Added a Diagnostics Center to the Govern workspace and Ctrl+K actions.
- Added shared scheduling, dependency, and retry-policy functions.
- Corrected retry behavior when retry delay is zero.
- Added a toolkit-independent background-task manager with progress reporting and cooperative cancellation.
- Integrated migration and diagnostics into desktop save/open and job execution.
- Integrated state migration, structural validation, shared scheduling, and JSONL diagnostics into the headless runtime.
- Expanded automated coverage from six to thirteen tests.
- Preserved all v0.37 capabilities, including restored governance workflows, observability, and rollback.

## Compatibility

The `.pdw` storage format remains backward compatible. Files loaded from older versions receive missing defaults and are saved as v0.38 after validation.
