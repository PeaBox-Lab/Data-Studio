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

## ADR-012: Analyses are a registry of typed functions over a prepared context

**Status:** Accepted

**Context.** Descriptive statistics and data-quality checks were previously console output
inside the monolith. The workspace, CLI and API must return identical numbers, the UI needs
metadata to generate forms, and statistical behaviour must be testable against references.
Hypothesis tests and models will arrive later, so the contract must not assume a particular
statistic.

**Decision.**

- An analysis is an `AnalysisDefinition`: a stable `name`, `title`, `category`,
  `description`, a `StepParam` list and a pure `run(context, params)` function.
- `AnalysisContext` carries the dataset name, a lazy `DuckDBTable`, the ordered pipeline
  `step_ids` and the engine name. It builds the `Provenance` for every result, so a result
  can always be traced to the exact data and pipeline that produced it.
- `AnalysisRegistry` validates parameter names, applies defaults and coerces values before
  execution. Unknown analyses and parameter errors raise `AnalysisError`; unexpected
  exceptions are wrapped so the API can return HTTP 400 with a clear message.
- Every analysis returns an `AnalysisResult` (typed tables, metrics, diagnostics, narrative,
  chart specs, provenance). No analysis prints, writes HTTP, or depends on a UI toolkit.
- Analyses run on the prepared pipeline output by default. A caller may pass
  `use_pipeline: false` (API) or `--raw` (CLI) to analyse a raw dataset instead.
- The API catalog (`GET /api/v1/analyses`) and the CLI catalog (`data-studio analyses`) are
  generated from the same registry the workspace uses to build its forms.
- Phase 2A analyses are computed on demand; storing `AnalysisResult` documents under
  `analyses/` is a later format decision.

**Consequences.**

- Adding an analysis is one pure module plus a registry entry; all surfaces pick it up.
- Parameters, defaults and validation stay consistent between steps and analyses because
  both use `StepParam`.
- Reference fixtures are pinned in Phase 2B for every inference and regression result. Result
  caching and persisted analysis documents remain future work and can be added without
  changing the `AnalysisDefinition` contract.
- Analyses are stateless, so they are safe to run from a request thread, a CLI process or a
  scheduled job.

---

## ADR-013: Inference is computed in-kernel with SciPy and NumPy

**Status:** Accepted

**Context.** Phase 2B adds hypothesis tests and regression models. The obvious shortcut is to
call `statsmodels` for everything, but that adds a large optional dependency, produces result
objects with inconsistent shapes, and hides diagnostics that the workspace must display. The
platform needs one typed result per analysis, stable reference-tested numbers, and a runtime
that installs with a single command on Windows.

**Decision.**

- SciPy is a core dependency; NumPy and pandas already are. `statsmodels` stays an optional
  `stats` extra and is used only to generate reference fixtures during development.
- Hypothesis tests use SciPy's distribution functions directly. t-tests, ANOVA, chi-square and
  proportion tests calculate their own confidence intervals, effect sizes and assumption
  diagnostics so the numbers and the explanations always travel together.
- OLS uses normal equations with `numpy.linalg.lstsq`, a pseudo-inverse covariance matrix and
  explicit rank-deficiency handling. Logistic regression uses Newton-Raphson/IRLS with the
  observed information matrix. Both emit coefficient tables, standard errors, confidence
  intervals, p-values and fit diagnostics.
- Multiple-comparison corrections (`none`, `bonferroni`, `holm`, `fdr_bh`) are implemented as
  a shared helper and applied to pairwise tests.
- Numeric table cells are rounded at the kernel boundary. Reference tests compare analytic
  outputs to pinned SciPy/`statsmodels` values within 1e-6.

**Consequences.**

- A normal install has no `statsmodels`/`patsy` dependency and `pip check` stays clean.
- Every model exposes the same typed tables, metrics, diagnostics and narrative as the
  descriptive analyses, so the API, CLI and workspace need no model-specific rendering.
- Missing values are handled by complete-case filtering per analysis and reported through the
  sample size and diagnostics.
- Categorical predictors are represented by numeric indicators for now; formula syntax and
  mixed-effects models remain future work.

---

## ADR-014: Charts and reports render from the typed result, not a chart library

**Status:** Accepted

**Context.** Phase 2C must turn an `AnalysisResult` into charts and a shareable report. A
JavaScript charting library would add a build step, a runtime download, and a second source
of truth for colours, ticks and escaping. The result must render identically in the workspace
API, the CLI and an offline HTML file, including on machines with no network access.

**Decision.**

- `ChartSpec` is the single contract. It gains an inline `data` field so the records a chart
  needs travel with the specification instead of referring back to a table that may no longer
  be in scope.
- `data_studio.kernel.chart_render` is a pure-Python SVG renderer for all eleven `ChartType`
  marks. It has no runtime dependency beyond the standard library, uses a fixed palette and
  deterministic "nice" ticks, supports light and dark themes, and escapes every text value.
- Both the chart API (`POST /api/v1/charts/render`) and the HTML exporter
  (`data_studio.reporting`) call the same renderer, so a chart cannot drift between surfaces.
- `render_html_report` embeds SVG, tables, metrics, diagnostics, narrative and provenance in
  one document with inline CSS and no scripts or external assets.
- Reports are written beneath the project's `reports/` directory. The path is validated with
  `safe_project_path`, so a report cannot escape the project folder.
- ARIMA is fitted by conditional sum of squares with NumPy/SciPy, matching the closed-form
  OLS AR(1) answer. `statsmodels` and `pymannkendall` are development-only fixtures, not
  runtime dependencies.

**Consequences.**

- A normal install stays small and `pip check` remains clean.
- Charts are safe for untrusted cell values because all text is escaped at the renderer
  boundary; only renderer-produced SVG is trusted markup.
- The renderer is intentionally static. Interactivity (filters, cross-highlight, drill) is a
  Phase 3 concern and can be added as another renderer over the same specs.
- The JavaScript-free report works in email, ticketing systems and archives. PDF export is
  still future work.

---

## ADR-015: Saved analyses are fingerprinted JSON, refreshed on demand

**Status:** Accepted

**Context.** Phase 2D must let an analyst keep an analysis, know when its inputs have changed,
and refresh it without a server daemon. Storing pickled objects or a live database would make
projects less portable and harder to audit. Re-running every analysis on every open would hide
staleness and waste time.

**Decision.**

- A saved analysis is a versioned JSON document under `analyses/<id>.json` holding the
  analysis name, typed parameters, dataset, `use_pipeline`, schedule, timestamps, an input
  fingerprint, an optional report path and the last typed `AnalysisResult`. Nothing executable
  is stored.
- The input fingerprint hashes the dataset checksum, the serialised pipeline when prepared
  data is used, the application version and the `use_pipeline` flag. A mismatch marks the
  result stale; a missing dataset marks it missing.
- Schedules are declarative (`manual`, `on_open`, `hourly`, `daily`) and evaluated on demand:
  project open, a workspace timer while the tab is visible, and `data-studio refresh --due`.
  The CLI is the supported entry point for Windows Task Scheduler or cron.
- Refreshes and saves write JSON atomically and optionally rewrite a self-contained report
  under `reports/`. Corrupt documents are skipped and surfaced as errors so one bad file
  cannot hide the rest.
- The workspace polls due schedules at most once a minute and offers explicit Refresh due and
  Refresh all actions.

**Consequences.**

- A project records what was analysed, against which inputs, and when; the same documents are
  readable by the API, CLI and tests without running analysis code.
- There is no background process to leak, so "scheduled" means "evaluated the next time a
  surface asks", which is a deliberate local-first trade-off.
- App-version changes invalidate results, so a kernel upgrade cannot silently keep
  old-model numbers labelled fresh.
- A future dashboard layer can reference saved-analysis ids without redefining their
  evaluation or storage.

---

## ADR-016: Dashboards query a semantic model through a safe measure DSL

**Status:** Accepted

**Context.** Phase 3A needs live dashboards, not a second copy of the analysis layer. A
dashboard must aggregate stored data by dimensions, apply shared slicers, and stay readable
and auditable. Letting project JSON carry raw SQL would make every project a code-execution
surface. Storing pre-computed tile values would make dashboards stale and hide their logic.

**Decision.**

- A project owns one semantic model at `models/semantic.json`: tables map dataset ids to
  names; relationships describe joins; dimensions name grouping columns; measures are typed
  aggregate expressions.
- Measures use a small DSL (`sum(amount)`, `sum(amount) / count()`,
  `sum(units) where region = 'North'`). The parser accepts a fixed aggregate set, arithmetic,
  parentheses and `where` predicates. It compiles to quoted DuckDB SQL with parameter values
  escaped as literals. Raw SQL is never read from a project file.
- Unqualified column references must resolve to exactly one table. Ambiguous names are
  rejected with a `SemanticError` and must be qualified as `table.column`; silently picking a
  table would make results depend on join order.
- `SemanticEngine` plans the `LEFT JOIN`s required by the referenced dimensions and measures
  along model relationships and executes one grouped SQL statement. Filters on dimensions are
  `WHERE`; filters on measures are `HAVING`. Only pipeline output for the model's
  `default_table` is used; other tables read their raw stored snapshots.
- Dashboards are versioned JSON under `dashboards/<id>.json` with KPI, chart and table tiles
  plus shared slicers. A tile is a semantic query and a presentation choice. Slicer
  selections become `in` filters on every tile; a tile that cannot join a slicer dimension
  returns its own error, so one incompatible tile cannot blank the dashboard.
- Tile rendering goes through the existing kernel chart renderer. The API, the workspace and
  the standalone HTML report consume the same `DashboardView`; no numbers or SVGs are stored
  in the dashboard document.
- The starter model is derived from recorded schemas only. Derivation never guesses
  relationships; a derived model is a safe starting point that a user edits.

**Consequences.**

- Dashboards are small, reviewable documents and inherit dataset checksums, schema
  validation and atomic writes from the project format.
- The same safety argument as pipelines holds: a dashboard can choose among vetted
  aggregates, dimensions and filters, but cannot execute arbitrary SQL or Python.
- Users pay for relationship mistakes at model or tile level with a clear message instead of
  a silently wrong join.
- A future report scheduler, dashboard grid layout or non-SQL semantic engine can build on
  the same model and query contracts without changing stored dashboards.
- Cross-table measures require a relationship; the engine does not invent one from matching
  column names.

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
