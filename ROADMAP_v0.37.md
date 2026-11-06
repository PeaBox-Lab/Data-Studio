# Data Studio Roadmap after v0.37

## v0.38 — Modular Application Core
- Split Workbench into UI shells, services, repositories, calculation modules, and report renderers.
- Unify GUI and headless job execution.
- Add typed project-state models and explicit schema migrations.
- Add background workers, progress, cancellation, and structured error reporting.

## v0.39 — Trusted Project and Model Packages
- Introduce a versioned ZIP/JSON/Parquet project format.
- Add manifest checksums, optional signatures, migration tooling, and import-only support for legacy pickle projects.
- Add safer model packaging and provenance metadata.

## v0.40 — Operations and Evidence Center
- Add structured application logs, correlation IDs, retention controls, exportable audit trails, and deployment trend charts.
- Add environment comparison, release timelines, threshold configuration, and richer rollback playbooks.

## v0.41 — UX and Accessibility
- Replace dense modal workflows with durable task pages.
- Add unified global search, contextual help, keyboard-first navigation, accessible labels, and tested light/dark themes.

## v0.42 — Extensibility
- Add a documented plugin interface for data sources, transformations, models, validators, charts, and deployment targets.
- Add isolated plugin execution and compatibility checks.
