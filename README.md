# Data Studio

A local-first analytics workbench for cleaning, joining, analyzing, visualizing and reporting on
tabular data. The long-term goal is one platform for the work people currently split across Excel,
R, SAS, Tableau and Power BI.

**Start here - the simple desktop app.** Data Studio now ships a small, friendly desktop app:
open a file, tidy it, chart it and save it. No jargon, no clutter - just five screens
(Home, My table, Clean, Charts, Save & share) with undo and a built-in help guide.

```powershell
.\run_data_studio.bat        # or: python -m data_studio.ui   (after pip install -r requirements.txt)
```

See [`USER_GUIDE.md`](USER_GUIDE.md) for the two-minute tour.

**Underneath** is the full analytics engine: kernel contracts, project format, CLI and API - phases 0-4
(typed analyses, time series, semantic dashboards, time intelligence, what-if parameters,
drill-down cross-filtering, Excel-like formula bar, pivots, code steps, reproducible script export). The v0.38 all-features prototype is
frozen in [`legacy/`](legacy/README.md) and kept runnable as a reference. See
[`REVIEW_AND_ROADMAP.md`](REVIEW_AND_ROADMAP.md) for the review, gap analysis and phased plan.

## Documentation

| Document | Contents |
|---|---|
| [`REVIEW_AND_ROADMAP.md`](REVIEW_AND_ROADMAP.md) | Measured review of v0.38, gap analysis, target architecture and phased plan. |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Architecture decision records: why the web app, kernel, engines and project format. |
| [`docs/PROJECT_FORMAT.md`](docs/PROJECT_FORMAT.md) | The versioned folder project format, paths, checksums and integrity rules. |
| [`USER_GUIDE.md`](USER_GUIDE.md) | Two-minute tour of the simple desktop app. |
| [`legacy/USER_GUIDE_v0.38.md`](legacy/USER_GUIDE_v0.38.md) | Guide to the frozen all-features prototype. |

## The pieces

| Path | Purpose |
|---|---|
| `data_studio/ui/` | The simple desktop app (`python -m data_studio.ui`). |
| `data_studio/` | The analytics package: kernel, project format, API, CLI. |
| `legacy/` | The frozen v0.38 all-features prototype. Reference only - do not add features. |

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## Run the new stack

```powershell
data-studio doctor
data-studio init .\examples\demo --name "Demo"
data-studio verify-project .\examples\demo
data-studio serve --project .\examples\demo --port 8765
```

Then open <http://127.0.0.1:8765>. Interactive API docs are at `/docs`.

### Import, clean, run and export headlessly

```powershell
data-studio add-dataset .\examples\demo .\data\sales.csv --id sales
data-studio add-step .\examples\demo trim_text --params '{"columns":["region"]}'
data-studio add-step .\examples\demo cast --params '{"column":"amount","dtype":"float","try_cast":true}'
data-studio add-step .\examples\demo drop_missing --params '{"columns":["amount"]}'
data-studio run .\examples\demo
data-studio export .\examples\demo .\data\clean.parquet
```

The same project opens in the browser workspace, where datasets, schemas, previews,
pipeline steps, analyses and exports are editable. Opening a project after a restart
reproduces the same results because datasets, schemas, checksums and the pipeline are all
on disk.

### Analyse the prepared data

Analyses run on the **prepared pipeline output** by default, so the numbers always match the
cleaned preview. Seventeen analyses ship across Phase 2A (descriptive), Phase 2B
(inference and regression) and Phase 2C (time series and forecasting):

| Analysis | What it answers |
|---|---|
| `descriptive_stats` | Count, missing, mean, standard deviation, quartiles and range per numeric column. |
| `value_counts` | Most frequent values with counts and percentages, including a missing bucket. |
| `crosstab` | Two-column cross-tabulation with counts or row/column/overall percentages. |
| `correlation` | Pairwise Pearson or Spearman correlation matrix. |
| `quality_profile` | Per-column completeness, distinct values, ranges, constant columns and duplicate rows. |
| `t_test` | One-sample, two-sample (Welch or Student) or paired means, with CIs and Cohen's d. |
| `anova` | One-way ANOVA with eta-squared, omega-squared and Tukey HSD comparisons. |
| `chi_square` | Independence test for two categorical columns with expected counts and Cramer's V. |
| `proportion_test` | One- or two-sample proportion z-tests with CIs and effect size h. |
| `pairwise_tests` | All pairwise group comparisons with Bonferroni, Holm or FDR correction. |
| `linear_regression` | OLS coefficients, standard errors, CIs, standardized betas, VIF and residual diagnostics. |
| `logistic_regression` | Binary logistic regression with odds ratios, ROC AUC, accuracy and separation checks. |
| `time_series_summary` | Inferred frequency, coverage, gaps, summary statistics and linear trend for a time series. |
| `acf_pacf` | Autocorrelation and partial autocorrelation functions with Ljung-Box tests. |
| `seasonal_decompose` | Classic additive or multiplicative trend, seasonal and residual decomposition with strength metrics. |
| `trend_test` | Mann-Kendall monotonic trend test with Sen's slope and tie correction. |
| `arima_forecast` | ARIMA(p,d,q) by conditional sum of squares, forecast intervals and an optional backtest. |

From the CLI:

```powershell
data-studio analyze .\examples\demo descriptive_stats --params '{"columns":["amount"]}'
data-studio analyze .\examples\demo linear_regression --params '{"response":"amount","predictors":["score"]}'
data-studio analyze .\examples\demo time_series_summary --params '{"time":"month","value":"revenue"}' --raw
data-studio report .\examples\demo arima_forecast --params '{"time":"month","value":"revenue","p":1,"d":0,"q":0}' --raw
```

The `report` command writes a self-contained HTML file (default
`reports/report-<timestamp>.html`) with inline SVG charts, tables, metrics, diagnostics,
narrative and provenance. Pass `--destination`, `--title` and `--theme dark` to customise it.

From the API:

```powershell
curl -X POST http://127.0.0.1:8765/api/v1/analyses/run `
  -H "Content-Type: application/json" `
  -d '{"analysis":"quality_profile","params":{}}'

curl -X POST http://127.0.0.1:8765/api/v1/charts/render `
  -H "Content-Type: application/json" `
  -d '{"charts":[{"chart_type":"column","title":"Revenue","encoding":{"x":"region","y":"amount"},"data":[{"region":"North","amount":10}]}]}'

curl -X POST http://127.0.0.1:8765/api/v1/analyses/report `
  -H "Content-Type: application/json" `
  -d '{"analysis":"time_series_summary","params":{"time":"month","value":"revenue"},"use_pipeline":false}'
```

Every analysis returns an `AnalysisResult` — typed tables, scalar metrics, diagnostics,
a plain-language narrative, chart specifications and provenance — so the workspace, CLI,
API and HTML reports render exactly the same numbers. Chart specifications carry their own
data and are rendered to SVG by the kernel; no charting library or network access is
needed. Pass `"use_pipeline": false` to analyse a raw dataset. Reports are written into
the project's `reports/` directory.


### Save, schedule and refresh analyses

Saved analyses keep a typed result under `analyses/<id>.json` and optionally a report at
`reports/<id>.html`. Each document records the analysis, typed parameters, dataset,
`use_pipeline`, schedule, timestamps and an **input fingerprint**: a hash of the dataset
checksum, the serialised pipeline (when using prepared data), the app version and the
`use_pipeline` flag. When an input changes the saved result is reported as *stale* until it
is refreshed.

```powershell
data-studio save-analysis .\examples\demo "Monthly revenue" descriptive_stats `
  --params '{"columns":["amount"]}' --schedule daily --report
data-studio saved .\examples\demo
data-studio saved .\examples\demo monthly-revenue
data-studio refresh .\examples\demo --due
data-studio refresh .\examples\demo --all
data-studio remove-saved .\examples\demo monthly-revenue
```

Schedules are `manual`, `on_open`, `hourly` and `daily`. They are evaluated when a project
opens, by the workspace's minute timer while the tab is visible, and by
`data-studio refresh --due` — which also refreshes due **dashboards** — so there is no
background daemon. Use `--due` from Windows Task Scheduler or cron for unattended refresh.
The API mirrors the CLI:

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/v1/saved-analyses` | List saved analyses with `status`, `due`, `next_refresh`. |
| `POST` | `/api/v1/saved-analyses` | Save an analysis, schedule and optional report. |
| `GET` | `/api/v1/saved-analyses/{id}` | Fetch the definition and last typed result. |
| `POST` | `/api/v1/saved-analyses/{id}/refresh` | Refresh one saved analysis. |
| `POST` | `/api/v1/saved-analyses/refresh` | Refresh `due` entries, or all with `{"due": false}`. |
| `GET` | `/api/v1/saved-analyses/{id}/report` | Download the stored HTML report artifact. |
| `DELETE` | `/api/v1/saved-analyses/{id}` | Delete the saved document. |

In the workspace, run an analysis and choose **Save analysis**; the **Saved** view shows
freshness, schedules and report downloads. Tooltips and the `?` help dialog explain
controls without expanding the layout, and the theme toggle follows your OS preference on
first use.

## Build semantic dashboards

Phase 3A adds a semantic layer between stored datasets and dashboards. A model at
`models/semantic.json` maps datasets to tables, dimensions (grouping columns) and metrics
(typed aggregates). Dashboards at `dashboards/<id>.json` reference those names; they never
store SQL, SQLite files or pre-computed numbers.

```powershell
data-studio model-derive .\examples\demo --name "Sales model"
data-studio model-show .\examples\demo
data-studio query .\examples\demo --dimensions region --measures total_amount `
  --order "total_amount desc"
```

Measures use a small, safe DSL rather than raw SQL:

```text
sum(amount)
sum(amount) / count()
sum(units) where region = 'North'
avg(amount) where sales.region in ('North', 'South')
```

`count()`, `sum`, `avg`, `min`, `max`, arithmetic, parentheses and `where` predicates are
supported. Unqualified columns that exist in more than one table are rejected; qualify them
as `table.column`. References are resolved against the model, quoted, and compiled to
DuckDB `SUM(...) FILTER (WHERE ...)` expressions, so a project file can never inject SQL.

A derived model includes every numeric column as `total_<column>` and
`average_<column>` measures and every string, boolean or temporal column as a dimension. It
does not guess relationships. Add them to `models/semantic.json` when a star schema is
needed; the query engine plans the required `LEFT JOIN`s and returns a per-tile error instead
of failing the dashboard when a tile cannot reach a slicer dimension.

```powershell
data-studio dashboard-save .\examples\demo `
  --title "Sales performance" `
  --tiles '[{"id":"total","title":"Total revenue","kind":"kpi","measures":["total_amount"]},
            {"id":"by-region","title":"Revenue by region","kind":"chart",
             "dimensions":["region"],"measures":["total_amount"],"chart":"column"}]' `
  --slicers '[{"id":"region","dimension":"region"}]' `
  --bookmarks '[{"id":"north","title":"North only","selections":{"region":["North"]}}]'
data-studio dashboard-render .\examples\demo sales-performance --select region=North
data-studio dashboard-report .\examples\demo sales-performance --select region=North
```

The workspace **Dashboards** view derives the model, composes KPI/chart/table tiles, applies
shared slicer chips to every tile, and downloads the current view as a self-contained HTML
report. Phase 3B adds presentation controls: tiles can sort and cap rows (`order_by`,
`limit`), table tiles can render data bars or a heatmap from server-normalised percentages,
and dashboards store named saved views (`bookmarks`) that restore slicer selections. The
workspace prints the canvas through the browser print dialog (save as PDF) and exports each
chart as a PNG; the HTML report carries the same conditional formatting and print styles.

Phase 3C completes the analytical core:

- **Calendar (date) table.** `data-studio date-table .\examples\demo --start 2020-01-01
  --end 2030-12-31 --rederive` generates a one-row-per-day calendar dataset (date, year,
  quarter, month, month_name, year_month, week, weekday, month/quarter/year/week starts)
  and, with `--rederive`, replaces the model so those columns become grain-tagged
  dimensions and a `Calendar` hierarchy (`year → quarter → month → day`). Deriving a
  model from an existing calendar marks grains automatically.
- **Time-intelligence measures.** Measures may wrap one aggregate: `running_total(sum(amount))`
  (cumulative sum ordered by the grain axis), `yoy(sum(amount))` (change vs the same period
  in the prior year) and `yoy_pct(sum(amount))` (percentage change). They are ordered by the
  date dimension in the query that carries a `grain` (`year`/`quarter`/`month`/`week`/`day`);
  without one the engine explains exactly what to do. Grain is set per dimension — the
  workspace model panel has a grain dropdown per date dimension, or set `grain` in
  `models/semantic.json`.
- **Drill-down and cross-highlighting.** Hierarchy tiles show breadcrumbs and drill on click
  (year → quarter → month → day). Clicking a mark on a chart, or the value in a table row,
  cross-filters every tile on the dashboard; the active filters appear as removable chips.
- **Tile tooltips.** Every tile has a **SQL** button that shows its dimensions, measures,
  elapsed time and the exact compiled SQL behind it.

Phase 3D makes dashboards operational:

- **What-if parameters.** Dashboards declare typed parameters (`number`, `text`, `choice`,
  `boolean`) with defaults and bounds. Tiles reference them as `@name` in filter values or
  the row cap (`limit`); viewers change them in the workspace parameter bar and every tile
  re-queries live. Bookmarks capture slicers; filters and parameters travel with the render.
- **Scheduled refresh.** Dashboards carry `schedule` (`manual`/`on_open`/`hourly`/`daily`),
  `refreshed_at` and an input fingerprint (model + dataset checksums + pipeline + app
  version). `data-studio refresh --due`, the workspace minute timer and project open all
  refresh due dashboards alongside saved analyses; the workspace shows fresh/stale pills,
  a schedule selector and **Refresh now**. With `report_path` set
  (`data-studio dashboard-save ... --report`), each refresh rewrites
  `reports/dashboard-<id>.html`.

```powershell
data-studio dashboard-save .\examples\demo --title "YoY overview" `
  --tiles '[{"id":"running","title":"Running total","kind":"chart","chart":"line",
            "dimensions":["month"],"measures":["running_total"],"order_by":[{"field":"month","direction":"asc"}]},
            {"id":"yoy","title":"YoY %","kind":"chart","dimensions":["month"],"measures":["yoy_pct"]}]' `
  --parameters '[{"name":"min_total","type":"number","default":0,"minimum":0}]' `
  --schedule daily --report
```

The same operations are available over HTTP:

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/v1/model` | Read the model, validation problems and available datasets. |
| `PUT` | `/api/v1/model` | Validate and replace the model. |
| `POST` | `/api/v1/model/derive` | Derive a starter model from recorded schemas. |
| `POST` | `/api/v1/model/date-table` | Generate a calendar (date) dataset for time intelligence. |
| `DELETE` | `/api/v1/model` | Remove the model. |
| `POST` | `/api/v1/model/query` | Run a typed dimension/measure query. |
| `GET` | `/api/v1/model/dimensions/{name}/values` | List slicer values with search. |
| `GET` / `POST` | `/api/v1/dashboards` | List or create dashboards. |
| `GET` / `PUT` / `DELETE` | `/api/v1/dashboards/{id}` | Read, replace or delete one dashboard. |
| `POST` | `/api/v1/dashboards/{id}/render` | Render tiles with slicer selections. |
| `POST` | `/api/v1/dashboards/refresh` | Refresh one dashboard with `{"id":…}`, `{"due": true}` or all. |
| `POST` | `/api/v1/dashboards/{id}/bookmarks` | Save a named slicer selection set. |
| `DELETE` | `/api/v1/dashboards/{id}/bookmarks/{bookmark_id}` | Delete a saved view. |
| `GET` / `POST` | `/api/v1/dashboards/{id}/report` | Download HTML, or write it under `reports/`. |

## Excel-like editing and scripting (Phase 4)

Phase 4 closes the gap with a routine spreadsheet workflow — all inside the workspace
(`data-studio serve`), the CLI or the desktop app, never on your original file.

- **Formula bar.** In Prepare, `fx` takes a typed expression (`amount * 1.2`,
  `upper(region)`, `round(amount / sum(amount), 4)`) and adds it as a `derive` step with
  live syntax checks. Targets: replace a column or create a new one. Quick actions:
  fill down (forward-fill missing), find &amp; replace, rename, cast, drop, copy preview
  as TSV.
- **Pivot.** A Pivot view builds row/column cross-tabs with one or more values, each
  with its own aggregation (sum, average, count, distinct count, min, max, median,
  stddev). Copy as TSV or push a single-value pivot into the pipeline as a `pivot` step.
- **Grid comforts.** Freeze panes (sticky header and first column) in every table;
  conditional formatting per column — **data bars**, **heatmap** and **highlight** with
  six comparison operators and red/amber/green/blue — persisted per dataset in the
  project (`formats/<dataset>.json`) and applied everywhere the grid renders. In the
  desktop app, **validation lists** restrict a column to an allowed set (dropdown
  editing; out-of-list values are rejected).
- **True escape hatch.** A **Python / R code step** runs trusted local scripts on the
  data as it flows through the pipeline. The script gets the input table as Parquet in
  `DATA_STUDIO_INPUT_PARQUET` and writes one Parquet table to
  `DATA_STUDIO_OUTPUT_PARQUET`; timeouts, failure output and exit codes are reported
  with the pipeline error. Python code runs inline on the desktop; R requires
  `Rscript` on the PATH.
- **Script export.** Freeze the current pipeline as a standalone script:
  `data-studio script .\examples\demo --format python --output exports\pipeline_export.py`.
  Every SQL step becomes DuckDB SQL; code steps are inlined and re-executed; dataset
  paths are relative to the project root. The Python script needs only `duckdb` and
  `pandas`; the R variant needs `duckdb`/`DBI`. `--format r` requires all code steps to
  be R.

From the API:

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/v1/formula/check` | Type-check an expression; returns the inferred type and a sample. |
| `POST` | `/api/v1/pivot` | Conditional-aggregate pivot (rows, one pivot column, values). |
| `POST` | `/api/v1/script` | Generate a reproducible Python or R script for the pipeline. |
| `GET` / `PUT` | `/api/v1/datasets/{id}/format` | Read or replace conditional-formatting rules. |

## Run the legacy prototype

```powershell
.\legacy\run_data_studio.bat
```

(See [`legacy/README.md`](legacy/README.md). Reference only.)

## Test

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check data_studio tests scripts
```

Phase 1 includes a reproducible scale benchmark:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_5m.py --rows 5000000 --reset
```

A 5M-row messy CSV run on the development laptop imported in 1.0 s, ran the 8-step
clean/join pipeline in 0.4 s, exported a 26 MB Parquet file in 1.4 s, and re-ran with
8/8 cache hits in 0.4 s.

## Architecture in one paragraph

All surfaces (web, CLI, scheduler, tests) call the same kernel. A `TableSchema` describes columns and
roles; an `AnalysisResult` carries typed tables, metrics, diagnostics, narrative and charts; a project
is a folder containing `project.json`, Parquet datasets and JSON definitions — never pickle. The
execution engine is DuckDB over Arrow, with pandas kept for small-data compatibility.
