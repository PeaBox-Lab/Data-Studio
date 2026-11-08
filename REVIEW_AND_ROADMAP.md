# Data Studio — Technical Review and Build Plan

**Reviewed:** v0.38 (`data_studio_v0.38.py` and the six supporting modules)
**Goal reviewed against:** a single platform for data modification, cleaning, processing, statistics, visualization and reporting — comparable in intent to SAS, R, Tableau, Power BI and Excel.
**Review type:** static code review + measured inventory + quality-gate run.

**Implementation status (updated):** Phase 0, Phase 1, Phase 2A and Phase 2B are complete.
The new package has the kernel contracts, DuckDB engine, project format, 31-step registry,
CLI, FastAPI surface and a redesigned browser workspace. Phase 2A added the analysis registry
and five typed descriptive analyses. Phase 2B added seven inference and regression analyses
(`t_test`, `anova`, `chi_square`, `proportion_test`, `pairwise_tests`, `linear_regression`,
`logistic_regression`) with reference-tested numbers, assumption checks, effect sizes,
confidence intervals and multiple-comparison corrections. The suite stands at 405 tests plus
the frozen v0.38 gate. Phase 2C–2D below are the recommended next slices.

---

## 1. Verdict

Data Studio v0.38 is an **impressive prototype with unusually broad domain coverage, built on an architecture that cannot carry a real product**.

The breadth is real: data import, cleaning, pipelines, SQL, DuckDB, charts, dashboards, statistics, engineering, AutoML, monitoring, APIs, scheduling, lineage, contracts and deployment governance all exist in some form. The problem is *how* they exist:

- **84% of the code is one class** (`Workbench`, 3,084 of 3,229 lines, 236 methods).
- Almost every "feature" is a modal dialog that computes inline and **prints text into the Python Lab console** (64 `setPlainText` calls, 109 `QInputDialog` calls).
- **89 handlers are not reachable from the visible tabs** — they only exist behind Ctrl+K, so the UI advertises far less than the code contains, while the docs advertise far more than either.
- **13 automated tests** exist, and none of them exercise the 236 methods where the actual product lives.
- Project state is one giant dictionary persisted with **`pickle`**, including fitted scikit-learn models. Opening a project can execute arbitrary code.
- Every transformation copies the **entire DataFrame** and keeps up to 20 full copies for undo. This guarantees memory failure on the "large data" story the app claims.

**Recommendation: stop adding features to the monolith.** Keep v0.38 working as a reference, and build the next version around a small, testable **analytics kernel** — schema + expression language + execution engine + step registry + typed analysis results — with the UI as a thin shell over it. This is a *strangler* migration, not a rewrite from zero.

---

## 2. What actually exists (measured, not claimed)

### 2.1 Code inventory

| File | Lines | Role | Tested? |
|---|---:|---|---|
| `data_studio_v0.38.py` | 3,229 | Everything: UI, domain logic, I/O, stats, ML, dashboards, API codegen | No |
| `data_studio_headless_v0.38.py` | ~800 | CLI orchestration; duplicates pipeline logic | Indirectly |
| `data_studio_core_v0_38.py` | 166 | Health scoring, validation rules, safe expression eval | Yes (6 tests) |
| `data_studio_state_v0_38.py` | 63 | Defaults, migration, shallow validation | Yes |
| `data_studio_diagnostics_v0_38.py` | ~50 | Structured events + JSONL | Yes |
| `data_studio_jobs_v0_38.py` | ~70 | Schedule/dependency/retry | Yes |
| `data_studio_tasks_v0_38.py` | ~60 | Background task manager | Yes |
| **Total** | **~4,400** | | **13 tests** |

Distribution: the entrypoint is **83.5%** of all application code. The "modular core" is 16.5%, and most of that is governance plumbing rather than analytics.

### 2.2 UI reality

| Measure | Count |
|---|---:|
| `Workbench` methods | 236 |
| Longest method (`select_tool`) | 156 lines (a hand-written dispatch dict) |
| Main-tab cards | 59 |
| Handlers reachable from main tabs | 59 |
| Handlers only reachable via Ctrl+K | 89 |
| `QInputDialog` modal prompts | 109 |
| `QMessageBox` calls | 140 |
| Analysis results written as plain text | 64 |
| `except Exception` handlers, mostly showing a message box | 69 |

Consequences:

- **No analysis result is a first-class object.** It is a string in a console, or a side effect on the grid. You cannot re-open a result, compare two runs, export a result table, schedule a result, or serve it from an API.
- **Discoverability is inverted.** "Hypothesis tests", "Regression", "Time patterns", "Correlation", "Outliers", "Feature engineering", "Compare models", "Forecast", "Cluster", "Sales/Finance/HR/Operations packs" and 70+ others exist but are invisible unless the user already knows to press Ctrl+K.
- **Dead or half-wired features.** `dashboard_cross_filter` is read during rendering but never written anywhere. No drill-down, tooltips, hierarchies, bookmarks or parameters exist despite the dashboard story.
- **Six chart types** (line, bar, scatter, histogram, box, correlation heatmap) in the chart builder; five in dashboards. Tableau/Power BI parity needs dozens plus interaction.

### 2.3 Capability depth (spot-checked)

| Area | What is implemented | Depth |
|---|---|---|
| Import | CSV, Excel, JSON, Parquet via pandas | Basic; no delimiter/encoding/sheet/header control, no DB/API sources |
| Export | CSV, Excel, Parquet | Basic |
| Cleaning | median/mode impute, dedupe, dtype coercion, trim text, IQR flags | Basic; no per-column strategies, regex, date parsing, scaling, outlier treatment |
| Pipelines | Named step list with ids, `depends_on`, operations, JSON export | Good seed; execution is sequential, no DAG scheduling, no caching |
| Join / reshape | Left join on one key; pivot with fixed count/mean/sum | Very limited |
| SQL | DuckDB against attached CSV/Parquet + SQL editor | Genuine and useful |
| Statistics | Welch t, one-way ANOVA, chi-square, Pearson/Spearman, paired t, effect size, CI, simple OLS diagnostics, Shapiro/Levene | Decent *starter set*; assumptions and interpretation are a strength |
| Regression | SciPy `linregress`; Random Forest; model comparison with CV | No OLS table, logistic, GLM, mixed models, robust SE, marginal effects |
| Time series | Hourly resample + 3-point rolling mean; 3-point moving-average "forecast" | Not a forecasting capability |
| ML | Random Forest default, KMeans(k=3), Isolation Forest, AutoML leaderboard | Functional demo, not a modeling platform |
| Visualization | 6 interactive chart types, static matplotlib; dashboard KPI/chart/table tiles, slicers | Static images; no interaction, filter widgets, or drill |
| Persistence | Single `.pdw` pickle of ~60 state keys and model objects | Fragile, unsafe, unversionable, unmergeable |
| Automation | Jobs, schedules, retries, headless runner, project lock | One of the better-engineered parts |
| Governance | Quality profile, rules, contracts, lineage, release gate, promotion, rollback | Broad; mostly metadata over the same weak core |

### 2.4 What the quality gate actually verifies

`data_studio_ci_v0.38.py` checks: required files exist, six modules compile, the governance method names exist, and 13 unit tests pass. It does **not** verify any data operation, statistic, chart, dashboard, import/export, or UI workflow.

I ran it here: compilation passed; the test run failed only because **no dependencies are installed in this environment** (pandas missing). That is not a code failure, but it shows the gate is environment-sensitive and the repo cannot currently reproduce its own "PASSED" validation report on a clean machine.

---

## 3. What is genuinely good (keep this)

These are real assets worth carrying forward:

1. **Domain map.** The feature taxonomy (Data → Transform → Analyze → Visualize → Model → Automate → Govern) is a sensible product skeleton. The v0.38 card structure is a usable information architecture.
2. **Pipeline step schema.** `{id, operation, parameters, depends_on, enabled}` is the right seed for a reproducible DAG.
3. **Safe expression validator.** `validate_expression` / `safe_math_eval` is a sound idea (AST allow-list, approved NumPy functions). It needs to become an expression *language*, not a security patch.
4. **Structured diagnostics with correlation IDs + JSONL.** Better than most prototypes.
5. **Job/task/retry modules.** Pure, testable, decoupled from Qt — exactly the pattern the rest of the app should follow.
6. **Headless runner.** Atomic writes, file locking, migration — a real foundation for scheduled processing.
7. **Statistical honesty.** Effect sizes, assumption checks, confidence intervals and plain-language caveats are present. That is rarer and more valuable than another chart type.
8. **Governance concepts.** Contracts, rules, lineage and release gates are differentiators if they operate on a real engine instead of being UI metadata.

---

## 4. Blocking problems (fix before adding features)

### P0 — Trust and data safety

| Problem | Evidence | Required fix |
|---|---|---|
| Arbitrary code execution on project open | `pickle.load` in `open_project` and headless `read_project`; models are pickled too | Replace `.pdw` pickle with a **folder project**: `manifest.json` + Parquet datasets + JSON definitions. Never unpickle. |
| No sandbox for Python Lab | `exec(code, {"__builtins__": __builtins__}, ...)` | Keep it as an explicit "trusted local code step", or run in a subprocess with a declared environment. Document it. |
| Undo stores full frame copies | `push_history` copies the whole DataFrame, max 20 | Undo must be **step-level** (drop/re-run the DAG), not snapshot-level. |
| Unbounded recomputation | `refresh_status` copies the frame and rebuilds chart columns on every edit; `apply_dataframe` calls `data_page()` twice | Separate model signals, debounce, and cache derived state. |

### P0 — Architecture

| Problem | Impact | Required fix |
|---|---|---|
| 3,084-line single class | Cannot test, reuse, parallelize, or expose analytics via API | Extract services by capability; `Workbench` becomes composition + widgets only |
| Logic fused with dialogs | Every analysis needs a human clicking; no headless or scheduled analytics | Engine functions take **data + params**, return **typed results** |
| Results are console text | No history, comparison, export, scheduling, or API | Introduce `AnalysisResult` (below) |
| Duplicated execution logic | Desktop and headless implement pipelines separately and drift | One `PipelineEngine`, two callers |
| No schema/type system | Column names are strings; dtype changes are ad hoc; join/validation errors are runtime surprises | `TableSchema` with logical + physical types, nullability, units, roles |

### P1 — Product quality

- **Discoverability:** surface the 89 hidden handlers, or delete them. Today the card grid and the code tell different stories.
- **UX:** 109 modal dialogs is a wizard, not a workspace. Move to durable panels with inline parameters and a results pane.
- **Large data:** the "DuckDB / Spark / 1M-row" story is undermined by pandas-first execution and full-frame copies. Choose **DuckDB + Arrow as default**, pandas as a compatibility path.
- **Statistics depth:** no nonparametric tests, multiple-comparison correction, logistic/GLM, power analysis, time series, PCA/factor analysis, survey weights, or missing-data strategy.
- **Visualization:** static matplotlib images cannot deliver Tableau/Power BI interaction. Needs a spec-driven renderer with filters, cross-highlighting, drill paths.
- **Reports:** no HTML/PDF/Word report that combines narrative, tables, assumptions and charts.
- **Testing:** 13 tests for 236 methods. No golden datasets, no statistical reference tests, no UI smoke tests, no performance budgets.
- **Packaging:** no `pyproject.toml`, no installable package, unpinned `>=` dependencies, `.bat` launchers only, no reproducible environment.
- **Docs inflation:** 18 release documents for a prototype, with claims ("retains all capabilities", "PASSED") that the tests do not substantiate. Replace with one roadmap, one changelog, and evidence produced by CI.

---

## 5. Gap analysis against the five reference tools

Legend: **●** usable · **◐** partial/demo · **○** missing

| Capability | SAS | R | Tableau | Power BI | Excel | Data Studio v0.38 |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| Tabular import/export | ● | ● | ● | ● | ● | ◐ |
| Database/API connectors | ● | ● | ● | ● | ◐ | ○ |
| Typed schema + data catalog | ● | ◐ | ● | ● | ◐ | ○ |
| Cleaning & imputation library | ● | ● | ◐ | ◐ | ◐ | ◐ |
| Reproducible pipeline/DAG | ● | ● | ◐ | ● | ○ | ◐ |
| Expression/formula language | ● | ● | ◐ | ● | ● | ○ (raw pandas string) |
| Window/analytic functions | ● | ● | ◐ | ● | ◐ | ○ |
| Joins/reshape/pivot depth | ● | ● | ◐ | ◐ | ● | ◐ |
| Descriptive & crosstab | ● | ● | ● | ● | ● | ◐ |
| Hypothesis testing suite | ● | ● | ○ | ○ | ○ | ◐ |
| Regression/GLM/mixed models | ● | ● | ○ | ◐ | ◐ | ◐ |
| Time series/forecasting | ● | ● | ○ | ◐ | ◐ | ○ |
| Multivariate (PCA/factor/cluster) | ● | ● | ○ | ○ | ○ | ◐ |
| Power/sample-size/design | ● | ● | ○ | ○ | ○ | ○ |
| Survey/weighted analysis | ● | ● | ○ | ○ | ○ | ○ |
| Survival/reliability | ● | ● | ○ | ○ | ○ | ◐ (basic) |
| Interactive dashboards | ◐ | ◐ | ● | ● | ◐ | ◐ |
| Filters/slicers/cross-filter/drill | ◐ | ◐ | ● | ● | ◐ | ○ (dead code) |
| Report authoring/export | ● | ● | ● | ● | ◐ | ○ |
| Scheduling/refresh | ● | ● | ● | ● | ○ | ◐ |
| Governance/lineage/contracts | ● | ◐ | ◐ | ● | ○ | ◐ |
| Extensibility/plugins | ● | ● | ◐ | ◐ | ◐ | ○ |
| Large-data execution | ● | ◐ | ● | ● | ○ | ◐ |

**Interpretation:** v0.38 has *breadth without depth*. Every row has a checkbox, few have a trustworthy implementation. That is the classic prototype trap: it looks complete in a demo and fails the first real dataset.

**Reality check:** full parity with these five products is a multi-year, multi-team effort (SAS alone is decades of statistical engineering; Tableau/Power BI are mature interaction engines). The winning strategy is a **narrow hero workflow executed deeply**, not five shallow clones.

---

## 6. Target architecture

### 6.1 Principles

1. **One engine, many surfaces.** Desktop, CLI, API and scheduler call the same functions.
2. **Analysis is data, not text.** Every operation returns a typed result object.
3. **Declarative first, code as escape hatch.** Pipelines, charts and dashboards are JSON specs. Python/R code steps are explicit and optional.
4. **The engine owns the schema.** Types, nullability and roles are validated before execution.
5. **Lazy by default, materialize on purpose.** DuckDB/Arrow for large data; small data can use pandas.
6. **Every operation is testable without a UI.**
7. **No unsafe persistence.** JSON manifests + Parquet. No pickle.

### 6.2 Package layout

```
data_studio/
  kernel/
    schema.py          # TableSchema, Column, DataType, role, nullability, units
    table.py           # Arrow-backed Table; lazy relation handle; preview(), collect()
    expr/
      parser.py        # safe expression DSL -> AST
      types.py         # type inference and coercion rules
      compile.py       # compile to DuckDB SQL / Polars / pandas
    engine/
      router.py        # choose duckdb | polars | pandas by size/operation
      duckdb_engine.py
      polars_engine.py
      pandas_engine.py
    dag.py             # pipeline graph, fingerprints, lineage, incremental re-run
  io/
    readers.py         # csv, excel, json, parquet, duckdb, sqlite, postgres, http
    writers.py
    sniff.py           # delimiter/encoding/header/schema inference
  steps/
    registry.py        # @step decorator: name, params schema, validate, apply, tests
    clean.py  transform.py  aggregate.py  join.py  reshape.py
    text.py   datetime.py   window.py    sample.py
  stats/
    results.py         # AnalysisResult, Estimate, TestResult, Table, Diagnostic
    descriptive.py  tests.py  anova.py  regression.py
    timeseries.py   multivariate.py  power.py  survey.py  reliability.py
    narrative.py       # plain-language interpretation from typed results
  viz/
    spec.py            # chart + dashboard spec (Vega-Lite compatible)
    render.py          # spec -> PNG/SVG/PDF and spec -> interactive payload
  ml/
    experiment.py  registry.py  explain.py  monitoring.py
  project/
    manifest.py        # versioned project.json, checksums, migrations
    store.py           # folder project: datasets/*.parquet, steps.json, analyses/*.json
  api/
    app.py             # FastAPI over the engine
  cli/
    main.py            # run project, run analysis, export, serve
  ui/                  # desktop shell (or web client)
    workbench.py       # composition + widgets only
    panels/ results/   # analysis result viewers, not consoles
```

### 6.3 The two contracts that matter most

**A. Typed analysis result** — replaces console text everywhere:

```python
@dataclass(frozen=True)
class AnalysisResult:
    kind: str                     # "ttest", "ols", "profile", ...
    title: str
    params: dict                  # inputs actually used (reproducibility)
    tables: list[Table]           # estimates, coefficients, group stats
    metrics: dict[str, float]     # p, effect size, R², n, ...
    diagnostics: list[Diagnostic]# assumptions checked, warnings
    narrative: str                # plain-language interpretation
    charts: list[ChartSpec]
    provenance: Provenance        # dataset fingerprint, step ids, timestamp, version
```

Every renderer (desktop panel, HTML report, REST JSON, notebook) consumes the same object. Every test asserts on `metrics` and `tables`, not on strings.

**B. Step registry** — replaces 33 scattered `apply_dataframe` call sites:

```python
@step("fill_missing", params={"column": Col, "method": Enum["median","mean","mode","zero","ffill","bfill"]})
def fill_missing(table: Table, params: FillMissing) -> Table:
    ...  # pure, schema-validated, engine-agnostic
```

The UI, pipeline DAG, headless runner and API all call the registry. Adding a capability means adding one tested function plus one card.

### 6.4 Project format (replaces pickle)

```
MyProject/
  project.json          # version, settings, active dataset, checksums
  datasets/
    sales.parquet
    customers.parquet
  steps/
    clean_sales.json    # ordered step specs + fingerprints
  analyses/
    2026-11-08-ttest-001.json   # full AnalysisResult
  dashboards/
    executive.json
  models/
    churn-v3/model.json + model.onnx (or joblib, opt-in)
  lineage.json
```

Benefits: diffable, mergeable, portable, inspectable, safe, cloud-storable. Datasets as Parquet. Analyses as evidence.

### 6.5 Execution strategy

| Data size | Engine | Why |
|---|---|---|
| < ~1M rows | pandas | ecosystem familiarity, speed for small ops |
| 1M–1B rows | DuckDB over Arrow/Parquet | vectorized SQL, out-of-core, zero-copy Arrow |
| distributed | optional PySpark plugin | only if a customer actually needs it |

Do not maintain three implementations by hand. Write operations against a small internal interface (or use `narwhals`) and let the router pick. Make DuckDB the default for anything beyond a preview.

### 6.6 Statistics design

The differentiator should be **trustworthy answers**, not test count:

- One `TestResult` contract: estimate, CI, test statistic, df, p, effect size, assumptions, decision, plain-language caveat.
- Assumption checks are automatic and block/warn appropriately.
- Multiple-comparison correction is built into ANOVA/post-hoc and any multi-test view.
- Reference tests compare outputs against R/SAS/statsmodels fixtures (stored expected values), so correctness is provable.
- Priorities: descriptive/crosstab → hypothesis suite → OLS/GLM → time series → multivariate → power/survey → survival.

### 6.7 Visualization and dashboards

- **Spec-driven**: a chart is data + encoding + mark + transforms (Vega-Lite model). The same spec renders to PNG for reports and to interactive output in the app.
- For a desktop app, embed a web view (QtWebEngine + Vega-Lite/ECharts) rather than fighting matplotlib for interaction. For a web product, render client-side.
- Dashboard features to reach "credible": filters/slicers, cross-highlighting, drill hierarchies, tooltips, top-N, conditional formatting, parameters, bookmarks, responsive grid, PDF export.
- Keep matplotlib only as a lightweight preview/export fallback.

### 6.8 UI direction

Two viable paths:

| Option | Pros | Cons |
|---|---|---|
| **A. Desktop, keep PySide6** | continuity, offline, local files, existing code | interaction-heavy dashboards are painful; packaging per-OS |
| **B. Local web app** (FastAPI + React/Svelte, launched from desktop) | modern viz ecosystem, easier collaboration/publish, one UI for local and server | rewrite of all screens; browser dependency |

**Recommendation:** engine first, so either UI can sit on it. If dashboards and sharing are central, choose B. If the product must stay a local desktop tool, keep A but move viz into a web view.

---

## 7. Phased plan

Assumes one full-time developer; multiply/divide for team size. Each phase ends with a demo and passing tests — not with a new markdown audit.

### Phase 0 — Stabilize and prepare (1–2 weeks)

**Status: complete.** Legacy gate passes; kernel contracts, project format and CLI/API shell exist.

- Install dependencies; confirm v0.38 launches and the gate passes on a clean machine.
- Add `pyproject.toml`, pin dependency ranges, add `pytest`, `ruff`, `mypy` (or pyright).
- Create `data_studio/` package; move the six modules in; keep `data_studio_v0.38.py` as a legacy entrypoint.
- Define `TableSchema`, `Table`, `AnalysisResult`, `ChartSpec`, `Provenance` dataclasses.
- Write 30–50 characterization tests around the *existing* pure functions (pipeline step execution, validation, health).
- Decide the persistence direction (folder project) and write the manifest schema.

**Done when:** clean install → gate passes; legacy app still launches; new package imports; schemas reviewed.

### Phase 1 — Kernel MVP (4–6 weeks)

**Status: implemented.** 359 tests; DuckDB lazy tables, 31 operations, fingerprints and
incremental reruns, folder projects, headless CLI, HTTP API and browser workspace. The
5M-row success criterion is measured by `scripts/benchmark_5m.py`: import 1.0 s, 8-step
clean/join 0.4 s, Parquet export 1.4 s, cached rerun 0.4 s on the development laptop.

- IO layer: CSV/Excel/JSON/Parquet/DuckDB with sniffing and schema preview; export.
- DuckDB-backed `Table` with lazy preview and `collect()`.
- Step registry with ~25 operations: filter, select/drop, rename, derive, fill/drop missing, dedupe, cast, trim/regex, date parse/extract, join (inner/left/right/full + cardinality check), union, group-aggregate, pivot/unpivot, sort, sample, window (lag/lead/rank/rolling).
- DAG with fingerprints, incremental re-run, and step-level undo.
- Folder project save/open with checksums and schema version.
- Headless CLI: `run`, `preview`, `export`.
- Minimal desktop shell: dataset list, schema panel, data grid, step list, result panel.

**Done when:** a messy 5M-row CSV can be imported, cleaned, joined and exported headlessly and in the UI; reopening reproduces the result; 200+ tests; no full-frame copies.

### Phase 2 — Statistics and results (4–6 weeks)

Phase 2 is delivered in four slices so the kernel, UI and correctness work stay coupled.

**Phase 2A — Typed result core and descriptive analytics (delivered).**

- `AnalysisResult` renderers: workspace result panel, JSON, CLI text.
- `AnalysisRegistry`, `AnalysisContext` and `AnalysisDefinition` with `StepParam`-based
  parameter validation and mandatory provenance.
- `descriptive_stats`, `value_counts`, `crosstab`, `correlation`, `quality_profile`.
- Typed API (`GET /api/v1/analyses`, `POST /api/v1/analyses/run`) and CLI
  (`data-studio analyses`, `data-studio analyze`).
- Redesigned workspace: Overview / Data / Prepare / Analyze / Export navigation, generated
  step and analysis forms, metric cards, diagnostics, narrative and typed tables.

**Phase 2B — Inference and model results (delivered).**

- Hypothesis suite: one-sample, two-sample (Welch/Student) and paired t-tests; one-way ANOVA
  with Tukey HSD; chi-square independence; one- and two-sample proportion tests; and
  all-pairs comparisons with Bonferroni, Holm and Benjamini-Hochberg corrections.
- Assumption checks: Levene, Shapiro-Wilk, expected-cell counts, VIF, Breusch-Pagan,
  Durbin-Watson and separation checks. Effect sizes include Cohen's d, Hedges' g, eta-squared,
  omega-squared, Cramer's V and proportion effect size h.
- OLS and binary logistic regression with coefficient tables, standard errors, confidence
  intervals, standardized betas, odds ratios, fit metrics and diagnostics. Implemented with
  NumPy/SciPy so no runtime `statsmodels` dependency is required.
- Reference fixtures generated independently with SciPy and `statsmodels` and pinned in
  `tests/test_inference.py` to 1e-6.
- Done: every inference result is typed, testable and reference-matched within tolerance.

**Phase 2C — Time series, charts and reports (3–4 weeks).**

- Time-series basics: decomposition, ACF/PACF, ARIMA/SARIMAX, backtest metrics.
- Chart spec renderer for 10 chart types, driven by `AnalysisResult.charts`.
- HTML report export (PDF next) that embeds the same typed results and chart specs.
- Done when: an analysis can be run, charted and exported as a shareable report without code.

**Phase 2D — Persistence and scheduling (2–3 weeks).**

- Persist `AnalysisResult` documents under `analyses/` with input fingerprints and cache keys.
- Refresh policy: re-run analyses when datasets or pipeline fingerprints change.
- Scheduled project refresh that writes results and report artifacts.
- Done when: a saved analysis survives restart, invalidates correctly and refreshes on a schedule.

**Phase 2 exit criteria:** at least 12 analyses produce typed, testable, exportable results
identical (within tolerance) to reference implementations, and the workspace, CLI and API
render the same numbers.

### Phase 3 — Semantic model and dashboards (6–8 weeks)

- Star-schema model: tables, relationships, measures (typed), hierarchies, date table.
- Interactive viz renderer: filters, slicers, cross-highlight, drill, tooltips, top-N, conditional formatting.
- Dashboard grid, parameters, bookmarks, PDF/PNG export.
- Scheduled refresh of datasets + analyses + dashboards.

**Done when:** a dashboard can be built without code, filtered interactively, refreshed on a schedule, and exported as a report.

### Phase 4 — Excel-like editing and scripting (4–6 weeks)

- Formula bar with the expression DSL (typed, compile to SQL/Polars), fill down, find/replace, bulk edits, clipboard, column ops.
- Pivot UI (rows/columns/values/filters, multiple aggregations).
- Conditional formatting, data validation lists, freeze panes.
- Python code step (subprocess, declared inputs/outputs) and optional R step.
- Script export: generate a reproducible Python/R script for any pipeline.

**Done when:** a user can replace a routine Excel workbook with a saved, reproducible project.

### Phase 5 — ML, scale and extension (ongoing)

- ML experiment contract, leaderboard, explainability, registry, monitoring (reuse the good v0.38 ideas).
- Plugin API for sources, steps, analyses, charts, targets.
- Performance budgets (10M rows), accessibility, packaging/installer, auth for API, collaboration/publish.

**Done when:** third-party steps can be installed safely and performance is measured, not claimed.

---

## 8. First sprint — concrete tickets

1. **Set up environment** — venv, install requirements, run app, run gate. Record exact versions. *(blocks everything)*
2. **Add `pyproject.toml`** + pinned ranges + pytest/ruff config.
3. **Create `data_studio/kernel/{schema,table,results,spec}.py`** with dataclasses and unit tests.
4. **Extract the pure pipeline executor** from `data_studio_v0.38.py` and `data_studio_headless_v0.38.py` into `kernel/dag.py` + `steps/registry.py`; make the headless runner use it. Delete the duplicate.
5. **Add a DuckDB `Table`** with `preview(n)` and `collect()`; port five steps (filter, derive, fill_missing, dedupe, group-by) and test on CSV/Parquet.
6. **Replace `push_history`** with step-level undo in a prototype screen.
7. **Write the project manifest spec** and a converter from an existing `.pdw` (read once, write folder). Do not read pickle after migration.
8. **Write 10 characterization tests** for existing statistics functions so the extraction cannot silently change results.
9. **Add a "Results" panel** that renders one `AnalysisResult` (e.g. Welch t-test) from a structured object instead of console text.
10. **Trim the docs**: one `ROADMAP.md`, one `CHANGELOG.md`; move release artifacts to `docs/releases/`; make CI generate the validation evidence.

Do not start new features until 1–6 are done.

---

## 9. Success metrics

Track these instead of feature counts:

| Metric | v0.38 | Target after Phase 2 |
|---|---:|---:|
| Largest file/class | 3,229 lines | < 600 lines |
| Tests | 13 | 405, with reference fixtures |
| Core logic coverage | ~0% | > 80% |
| Analyses returning typed results | 0 | 12 (Phase 2A + 2B) → 18+ |
| Operations usable headlessly & via API | partial | all |
| Time to clean+analyze a 1M-row CSV | untested | < 60 s on a laptop |
| Peak memory on 1M-row pipeline | ~full-frame × 21 | bounded / out-of-core |
| Reopen project on clean machine | unsafe pickle | guaranteed, no code execution |
| Visible vs implemented actions | 59 / 148 | 100% |

---

## 10. Risks and decisions

**Risks**

- **Scope.** Five-tool parity is not a v1 goal. Pick a hero workflow and defend it.
- **Rewrite temptation.** A clean rewrite loses years of accumulated domain detail. Strangle the monolith instead.
- **Statistics correctness.** A wrong p-value is worse than a missing feature. Reference tests are non-negotiable.
- **Dashboard interaction.** Matplotlib cannot deliver Tableau-class UX; decide the rendering stack early.
- **Performance claims.** The large-data story must be benchmarked on real files before it is marketed.
- **Legal.** Replicate functionality, not trademarks, iconography or proprietary file formats.

**Decisions needed before Phase 1**

1. **Primary surface:** local desktop (PySide6), local web app, or both?
2. **Hero workflow:** which user and task is v1 judged on? (Suggested: analyst with messy Excel/CSV exports who needs a defensible answer and a shareable dashboard.)
3. **Visualization stack:** web-view renderer (recommended for dashboards) vs matplotlib-only.
4. **Scope order:** statistics depth first, or dashboard/BI first? (Suggested: statistics + cleaning first; it is the defensible differentiator.)
5. **Compatibility:** must `.pdw` projects be readable forever, or is a one-time import acceptable?

---

## 11. Bottom line

The code proves you can build the breadth. The next version must prove **depth, correctness and testability**.

The single highest-leverage change is not a new feature — it is the **`AnalysisResult` + step-registry kernel**. Get that right and every capability you already imagined (desktop, API, scheduler, report, dashboard) becomes cheap. Skip it and each new feature makes the monolith harder to change and the product harder to trust.

**Start by extracting, not adding.**
