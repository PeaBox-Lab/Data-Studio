# Architecture decisions

This file records the decisions that shape Data Studio v0.39 and later. Each entry is an
Architecture Decision Record (ADR): the context, the decision, its consequences and the
status. Add new decisions at the bottom; do not rewrite history.

The review that motivated these decisions is in
[`REVIEW_AND_ROADMAP.md`](../REVIEW_AND_ROADMAP.md).

---

## ADR-001: The primary surface is a local web application

**Status:** Accepted

**Context.** The v0.38 prototype is a 3,229-line PySide6 desktop application with one
3,084-line `Workbench` class. Dashboards, cross-filtering and shareable reports are
central to the product, and those are expensive to build well with Qt widgets while the
web ecosystem already provides mature charting, tables and layout.

**Decision.** Build the new product as a local web application: a FastAPI backend and a
React/Vite frontend served from `localhost`. The desktop prototype stays runnable as a
reference but receives no new features. The kernel remains UI-agnostic so a desktop shell
can be added later if it is ever needed.

**Consequences.**

- The browser is the initial client; local files are read and written by the backend.
- The API is a first-class product surface, not an afterthought.
- The old desktop UI is not the migration target; it is a behaviour reference only.
- One UI can serve both local and shared/server deployments.

---

## ADR-002: Strangle the monolith; do not rewrite it

**Status:** Accepted

**Context.** The v0.38 monolith contains accumulated domain detail that would be lost in
a clean rewrite, and a full rewrite delays any usable release. At the same time, adding
more features to the monolith makes every change harder and riskier.

**Decision.** Use a strangler migration. New capabilities are built in the `data_studio/`
package behind contracts. Legacy modules are frozen, tested for characterization, and
retired only when their behaviour has been reimplemented and verified.

**Consequences.**

- The legacy desktop app remains runnable during the migration.
- No feature work goes into `data_studio_v0.38.py` or its `*_v0_38.py` helpers.
- Characterization tests (`tests/test_*_v0_38.py`) protect the behaviour being replaced.
- A capability is "migrated" only when the new path has tests and the old path is unused.

---

## ADR-003: A project is a folder, never a pickle

**Status:** Accepted

**Context.** v0.38 stored projects with `pickle`. Opening an untrusted project could
execute arbitrary code, projects were not diffable or mergeable, and a single corrupt
file could destroy all work. Correctness and trust are the product's differentiator.

**Decision.** A project is a directory containing `project.json`, Parquet datasets and
JSON definitions. Checksums and relative paths are validated on open. The manifest is
written atomically. `pickle` is forbidden for project persistence.

**Consequences.**

- Opening a project cannot execute code.
- Projects are inspectable, diffable and portable.
- The manifest is the root document; all other files are addressed relative to it.
- Dataset formats use native suffixes (`.parquet`, `.csv`, `.json`, `.xlsx`).
- See [`PROJECT_FORMAT.md`](PROJECT_FORMAT.md) for the exact format.

---

## ADR-004: The two core contracts are `AnalysisResult` and the step registry

**Status:** Accepted

**Context.** v0.38 analyses printed text into console-like panels. The same values were
recomputed by different code paths, could not be tested without a UI, and could not be
serialized as evidence. Data operations were scattered across dozens of
`apply_dataframe` call sites.

**Decision.**

1. Every analysis returns a typed `AnalysisResult` containing parameters, result tables,
   scalar metrics, diagnostics, a narrative, chart specs and provenance.
2. Every data operation is a registered step with declared parameters, validation and a
   typed input/output table contract.

All surfaces (web, API, CLI, scheduler, reports, tests) consume these two contracts.

**Consequences.**

- Tests assert on metrics and tables, never on formatted strings.
- A result can be stored, replayed and rendered without rerunning the analysis.
- Adding a capability means adding one tested step and one UI binding.
- Console text is never the source of truth.

---

## ADR-005: DuckDB over Arrow is the default execution engine

**Status:** Accepted

**Context.** The product must handle both small spreadsheets and large extracts.
Maintaining separate pandas, Polars and SQL implementations by hand would multiply bugs
and make results inconsistent.

**Decision.** Use DuckDB over Arrow/Parquet as the default engine for anything beyond a
preview. Keep pandas as a compatibility layer for small data, Excel/CSV ingestion and
legacy workflows. Operations are written against a small internal table interface so an
alternative engine can be added without changing callers.

**Consequences.**

- The `Table` protocol is the engine boundary (`schema`, `count`, `preview`, `collect`,
  `fingerprint`).
- Large data stays lazy; only previews and small results are materialized.
- pandas is not the reference implementation for statistical correctness.
- Engine selection is observable and recorded in provenance.

---

## ADR-006: Schemas own names, types, nullability and roles

**Status:** Accepted

**Context.** Type inference and column meaning were implicit and inconsistent in v0.38.
Charts and analyses guessed which columns were measures, dimensions or times.

**Decision.** `TableSchema`, `Column`, `DataType` and `Role` are kernel contracts. Names
are unique case-insensitively, types are logical rather than storage-specific, and roles
describe intended use (`dimension`, `measure`, `identifier`, `time`, ...). Schemas are
immutable, hashable and carry a stable fingerprint.

**Consequences.**

- Engines validate data against the schema before execution.
- Charts and analyses choose sensible defaults from roles, not name heuristics.
- Schema changes are explicit and visible in provenance.
- Result tables and stored datasets can be interpreted without the original data.

---

## ADR-007: Charts and dashboards are data, not imperative drawing code

**Status:** Accepted

**Context.** v0.38 dashboard definitions were embedded in application state and rendered
imperatively. The same chart could not be exported, reproduced or tested consistently.

**Decision.** Charts and dashboards are versioned specifications: data reference, mark,
encodings, transforms and layout. A renderer turns a spec into an interactive view or a
static export. Specs are stored as JSON and are independent of the UI framework.

**Consequences.**

- The same spec renders in the app, a report and an export.
- Dashboard layout and interaction can be validated without a browser.
- The rendering library can change without changing stored projects.
- `ChartSpec` and `DashboardSpec` are part of the kernel.

---

## ADR-008: Provenance is mandatory for analyses

**Status:** Accepted

**Context.** Users need defensible answers. A number without its inputs, dataset version
and code version cannot be audited or reproduced.

**Decision.** Every `AnalysisResult` carries `Provenance`: dataset identity, dataset
fingerprint, ordered step ids, a hash of the parameters, timestamp and application
version. Result ids are derived from the analysis kind, parameters, dataset fingerprint
and step ids.

**Consequences.**

- Results are reproducible and comparable across runs.
- Re-running with identical inputs produces the same result id.
- Stored analyses are evidence, not screenshots.
- Data changes are detectable through fingerprint mismatch.

---

## ADR-009: Safety boundaries are enforced at the data boundary

**Status:** Accepted

**Context.** A local-first tool still opens files from other people. Manifest paths,
dataset names and expressions are untrusted input.

**Decision.**

- Manifest paths must be relative POSIX paths, canonical, and must stay inside the
  project directory. Absolute paths, drive letters, backslashes and `..` are rejected.
- Dataset ids are restricted to a safe character set.
- Every dataset has a SHA-256 checksum verified on demand.
- The manifest is written atomically with `fsync` and `os.replace`.
- Expressions use an allow-list AST; arbitrary attribute access, imports and builtins are
  rejected.

**Consequences.**

- A malicious project cannot read or write outside its directory.
- Corruption and tampering are detectable.
- Interrupted saves do not leave a half-written manifest.
- Calculated columns cannot execute arbitrary Python.

---

## ADR-010: Python 3.11+, typed public APIs, and testable packaging

**Status:** Accepted

**Context.** The legacy app depends on a global interpreter and manual script launches.
That makes environments hard to reproduce and the code hard to consume from other tools.

**Decision.** The new package requires Python 3.11 or newer, ships a `pyproject.toml`,
exposes a `data-studio` console script, uses `pytest` and `ruff` as the quality gate, and
types the public kernel and project APIs. Runtime dependencies are explicit; optional
capabilities are extras (`stats`, `ml`, `excel`, `viz`, `desktop`).

**Consequences.**

- A fresh checkout can be installed with `pip install -e ".[dev]"`.
- CI can run without a display server.
- The kernel is importable from notebooks, scripts, the API and tests.
- Optional heavy dependencies do not burden the base install.

---

## ADR-011: Pipelines are ordered immutable steps with fingerprint-based reruns

**Status:** Accepted

**Context.** Users need to edit a cleaning pipeline, see results quickly, undo a mistake
and reopen the project later with identical output. A general DAG editor is not needed yet.

**Decision.**

- A project has one ordered pipeline in `steps/pipeline.json`; steps are operation names
  plus validated JSON parameters.
- `Pipeline` and `PipelineStep` are immutable; position changes, replacement, enablement
  and undo return new values.
- Each step fingerprint covers its operation, parameters, the previous step's fingerprint,
  any joined/unioned dataset fingerprint, the pipeline schema version and the app version.
- The runner caches lazy `DuckDBTable` values by fingerprint. A cache hit reuses the lazy
  query; a changed step invalidates that step and every later step.
- Results are never materialised until a preview, count or export asks for rows.

**Consequences.**

- Editing one step reruns only that step and its successors.
- Undo, disable and reorder do not mutate history; they change the next fingerprint chain.
- Reopening a project reproduces results from disk, but the in-memory cache starts cold.
- A future DAG editor can replace the linear chain without changing the step contract.

---

## Open decisions

These are deliberately deferred; record the answer here when it is made.

| Decision | Needed by | Notes |
|---|---|---|
| Interactive chart renderer | Phase 3 | Vega-Lite/ECharts vs a React chart library; specs stay renderer-neutral. |
| Project migration from v0.38 `.pdw` | Phase 1 | One-time importer vs permanent read support. |
| Authentication for non-local use | Phase 5 | Local mode has no auth; shared mode requires it before exposure. |
| Distributed execution | Phase 5 | Only if a real dataset requires it; DuckDB handles most cases. |
| Model artifact format | Phase 5 | Prefer ONNX; joblib only as an explicit opt-in. |
