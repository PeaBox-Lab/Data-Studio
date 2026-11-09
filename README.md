# Data Studio

A local-first analytics workbench for cleaning, joining, analyzing, visualizing and reporting on
tabular data. The long-term goal is one platform for the work people currently split across Excel,
R, SAS, Tableau and Power BI.

**Current status:** Phase 0 (foundation), Phase 1 (kernel MVP), Phase 2A (typed
descriptive analytics), Phase 2B (inference and regression), Phase 2C (time series,
chart rendering and HTML reports), Phase 2D (saved analyses, fingerprint
invalidation, scheduled refresh and report artifacts) and Phase 3A (semantic model,
safe measure DSL, live dashboards with slicers and dashboard HTML export) are
implemented. The new package can
import CSV/Excel/JSON/Parquet/DuckDB files, run an ordered, validated pipeline with
incremental reruns and undo, profile and summarise the result, run hypothesis tests,
regressions, time-series decompositions and ARIMA forecasts through the same typed analysis
registry, render charts as dependency-free SVG, export self-contained HTML reports, persist
results with freshness tracking, derive a star-schema semantic model, compose live dashboards
from typed dimensions and measures, and reopen a project reproducibly. The browser workspace
has a light/dark theme, a grouped and searchable analysis catalog, contextual help, a
Saved view for freshness, schedules and report downloads, and a Dashboards view with
shared slicers and one-click HTML export. v0.38 remains a frozen desktop
prototype kept runnable as a reference. See
[`REVIEW_AND_ROADMAP.md`](REVIEW_AND_ROADMAP.md) for the review, gap analysis and phased plan.

## Documentation

| Document | Contents |
|---|---|
| [`REVIEW_AND_ROADMAP.md`](REVIEW_AND_ROADMAP.md) | Measured review of v0.38, gap analysis, target architecture and phased plan. |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Architecture decision records: why the web app, kernel, engines and project format. |
| [`docs/PROJECT_FORMAT.md`](docs/PROJECT_FORMAT.md) | The versioned folder project format, paths, checksums and integrity rules. |
| [`USER_GUIDE_v0.38.md`](USER_GUIDE_v0.38.md) | Guide to the frozen desktop prototype. |

## The two codebases

| Path | Purpose |
|---|---|
| `data_studio_v0.38.py` + `data_studio_*_v0_38.py` | The legacy desktop prototype. Kept runnable as a reference. Do not add features. |
| `data_studio/` | The new package: kernel contracts, project format, API, CLI. |

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
`data-studio refresh --due`; there is no background daemon. Use `--due` from Windows Task
Scheduler or cron for unattended refresh. The API mirrors the CLI:

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
  --slicers '[{"id":"region","dimension":"region"}]'
data-studio dashboard-render .\examples\demo sales-performance --select region=North
data-studio dashboard-report .\examples\demo sales-performance --select region=North
```

The workspace **Dashboards** view derives the model, composes KPI/chart/table tiles, applies
shared slicer chips to every tile, and downloads the current view as a self-contained HTML
report. The same operations are available over HTTP:

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/v1/model` | Read the model, validation problems and available datasets. |
| `PUT` | `/api/v1/model` | Validate and replace the model. |
| `POST` | `/api/v1/model/derive` | Derive a starter model from recorded schemas. |
| `DELETE` | `/api/v1/model` | Remove the model. |
| `POST` | `/api/v1/model/query` | Run a typed dimension/measure query. |
| `GET` | `/api/v1/model/dimensions/{name}/values` | List slicer values with search. |
| `GET` / `POST` | `/api/v1/dashboards` | List or create dashboards. |
| `GET` / `PUT` / `DELETE` | `/api/v1/dashboards/{id}` | Read, replace or delete one dashboard. |
| `POST` | `/api/v1/dashboards/{id}/render` | Render tiles with slicer selections. |
| `GET` / `POST` | `/api/v1/dashboards/{id}/report` | Download HTML, or write it under `reports/`. |

## Run the legacy prototype

```powershell
.\.venv\Scripts\python.exe -m pip install PySide6 matplotlib scipy scikit-learn openpyxl
.\.venv\Scripts\python.exe data_studio_v0.38.py
```

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
