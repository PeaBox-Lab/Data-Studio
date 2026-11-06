# Data Studio Architecture, Process, Logic, GUI and UX Audit

## Executive conclusion

v0.36 had broad functional coverage but its 3,028-line GUI module combined 212 Workbench methods, persistence, calculations, rendering, automation, governance, and deployment concerns. More critically, sixteen governance actions were referenced by navigation and command-palette code but were not implemented. v0.37 repairs those paths and begins modularization with a pure, testable core.

## Architecture findings

### Critical findings fixed in v0.37
- Restored all missing governance handlers referenced by the GUI.
- Corrected exported job runners that still searched for a v0.21 runtime.
- Corrected headless metadata that still described v0.24.
- Added backward-compatible defaults for new persisted fields.
- Moved deployment health, promotion readiness, validation inference/execution, expression validation, and workspace health into `data_studio_core_v0_37.py`.

### Remaining structural debt
- The desktop controller remains a large monolith; UI, domain services, persistence, and reporting should be separated.
- `.pdw` and model files use pickle. This preserves compatibility but is unsafe for untrusted files.
- Most operations are synchronous on the GUI thread, so large data, model training, and exports can freeze the interface.
- Many dialogs use message boxes and text reports rather than durable task-oriented pages.
- State is represented by loosely structured dictionaries; typed schemas and migrations are needed.

## Actions and process audit

### Data lifecycle
Import, edit, transform, analyze, visualize, model, deploy, monitor, automate, govern, release, and promote are represented. v0.37 adds an explicit governance path and evidence sequence instead of leaving advanced actions discoverable only through Ctrl+K.

### Automation
The headless runtime has project locking, atomic writes, dependencies, retries, due-job ordering, blocked-job evidence, and JSON output. GUI execution and headless execution still duplicate job logic and should converge on one service.

### Release governance
The intended chain is contract tests → CI evidence → release gate → TEST promotion → PROD promotion. v0.37 enforces those PROD prerequisites and persists blocked and successful promotions.

### Rollback
v0.37 records deployment observations, explains every deduction, recommends review at configured thresholds, and changes environment release pointers only after confirmation. Infrastructure rollback remains external by design.

## Calculation audit

- Deployment health is deterministic and bounded from 0 to 100; component deductions are retained for explanation.
- PSI monitoring uses warning 0.10 and critical 0.25 thresholds.
- Statistical procedures generally use alpha 0.05, but alpha is hard-coded and should become a project setting.
- KPI percent formatting assumes the aggregate is a fraction; users can misformat already-percent values.
- Data quality in the sidebar is completeness-only, while the detailed profiler also considers duplicate and structural risks.
- Direct math-expression evaluation was a security and reliability risk. v0.37 validates the expression AST and approved names/functions before execution.
- DataFrame calculated expressions are now validated before pandas evaluation.

## GUI, interface and usability audit

### Strengths
- Familiar desktop layout, clear dataset sidebar, searchable Ctrl+K actions, editable grid, and task-oriented section tabs.
- Consistent status feedback and a reusable visual language.
- v0.37 adds a dedicated Govern section so governance is visible rather than hidden.

### Gaps
- The number of cards and modal dialogs creates cognitive load.
- Reports rendered into Python Lab blur analysis output and developer tooling.
- Dark-theme application appends styles repeatedly and needs a theme service.
- Long-running work lacks progress, cancellation, and background execution.
- Accessibility needs keyboard traversal testing, scalable contrast tokens, and screen-reader labels.

## Security and reliability

Only trusted pickle files should be opened. Exported APIs also load pickled models. Future releases should introduce signed packages, restricted deserialization, schema validation, checksums, structured logs, and secret scanning.
