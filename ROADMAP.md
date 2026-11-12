# Data Studio roadmap (friendly desktop app)

The goal is one local-first app that anyone can open without training: **import → clean → analyze → chart → share**. The legacy all-features prototype lives in `legacy/` and is kept runnable but frozen.

## Release tracking

| Release | Status | What it adds |
|---------|--------|--------------|
| 0.1 (current) | Done | Simple UI: Home, My table, Clean, Charts, Save & share |
| 0.2 | Done | **Analyze** screen — all 17 built-in analyses with friendly names and parameter forms |
| 0.3 | Done | **Statistics** — t-test, ANOVA, chi-square, proportion test, linear & logistic regression, time-series (summary, trend, decomposition, ARIMA) — all exposed on the Analyze screen |
| 0.4 | Done | **Process** — real data processing steps: filter, cast, rename, derive, pivot, unpivot, group & aggregate, dates, window functions, rolling operations, sampling |
| 0.5 | Done | **Reports** — Save analysis results as standalone HTML reports from the Analyze screen |
| 0.6 | Done | **Workspace dashboards** — calendar & time intelligence (running total, YoY), drill-down + cross-filter, what-if parameters, scheduled dashboard refresh |
| 0.7 | Done | **Phase 4** — formula bar (typed DSL, fill down, find/replace, TSV copy), Pivot view (rows/values/columns with per-value aggregations), conditional formatting (data bars, heatmap, highlight) persisted per dataset, desktop validation lists, Python/R code steps, and reproducible script export (Python or R) |

## Phase 0.2 — Analyze (done)

**Problem:** The app can tidy and chart data, but it can't yet *answer questions* about it ("are these two machines different?", "what's missing?", "is temperature correlated with output?").

**What was built:**
- New **Analyze** screen next to Clean.
- Chooser lists all 17 built-in kernel analyses with friendly, plain-English names ("Summarize my data", "How often do values happen?", "Are two groups different?").
- Selecting an analysis builds only the parameter widgets it needs (combo boxes for columns/enums, spin boxes for numbers, checkboxes for booleans) with sensible defaults from the loaded DataFrame.
- **Run analysis** validates against the built-in registry, executes against the table currently loaded on My table, and renders: one plain-English result sentence, headline metrics, warnings/info as plain text, and the first rows of the result table.

**Analyses exposed:**
- Summary: Descriptive statistics, Value counts, Crosstab, Correlation matrix, Data-quality profile
- Inferential: t-test, One-way ANOVA, Chi-square, Proportion test, Linear regression, Logistic regression, Pairwise comparisons
- Time-series: Time-series summary, Mann-Kendall trend, Autocorrelation (ACF/PACF), Seasonal decomposition, ARIMA forecast

**What's still needed for a polished 0.2/0.3:**
- ~~Result tables should render as a scrollable table widget instead of a text preview.~~ — done: result tables now render as scrollable `QTableWidget`s with title, up to 200 rows, row count and note.
- ~~Chart output from analyses (some analyses emit ChartSpec) is not yet rendered.~~ — done: analyses that emit `ChartSpec` are drawn on the Analyze screen as SVG images.

## Phase 0.3 — Statistics (done via Analyze)

- Two-sample t-test, ANOVA, chi-square, proportion test
- Linear & logistic regression with coefficients + R²
- Time-series summary, Mann-Kendall trend, ACF/PACF, seasonal decomposition, ARIMA forecast
- All exposed on the Analyze screen with parameter forms.

Note: These are already wired into the Analyze screen. Future releases can add polish (effect-size emphasis, grouped result tables, chart rendering).

## Phase 0.4 — Process (done)

**Problem:** The Clean screen was good for one-click fixes, but users need real data processing steps: cast types, rename, select/drop columns, filter rows, derive a calculated column, split/join text, parse/extract dates, pivot/unpivot, group & aggregate, sample, limit.

**What was built:**
- New **Process** screen next to Clean.
- Lists all 29 built-in kernel steps with friendly labels ("Filter rows", "Add calculated column", "Pivot wider", "Keep first N rows", etc.) grouped by category (clean, text, math, datetime, reshape, aggregate, window, sample).
- Selecting a step builds only the parameter widgets it needs with sensible defaults from the loaded DataFrame.
- Premium parameters are supported: `aggregations` parses shorthand like `output:sum, temperature:mean` into kernel objects; `mapping` parses `old=new, old2=new2` for rename.
- Steps execute through the existing kernel `DEFAULT_REGISTRY` (DuckDB engine) so the same numbers are produced everywhere.
- Changes go through the undo stack (`apply_change`), so Ctrl+Z / 'Undo last change' works.

**Steps exposed:**
- Clean: drop_missing, fill_missing, flag_outliers, deduplicate
- Text: trim_text, replace_text, split_text, extract_text, normalize_text, concat_columns
- Transform: cast, derive, select, drop, rename, sort, distinct
- Date/Time: date_parse, date_extract, date_trunc, date_shift
- Reshape: pivot, unpivot
- Aggregate: group_aggregate, value_counts
- Window: window (lag, lead, row_number, rank, dense_rank, cumsum, rolling)
- Sample: sample, limit

Not exposed (requires a second dataset in project mode): join, union.

## Phase 0.5 — Reports (done)

**Problem:** Analysis results were visible on the screen but could not be shared without copy-pasting the narrative.

**What was built:**
- "Save report as HTML..." button on the Analyze screen.
- Enabled after running an analysis; captures the last `AnalysisResult`.
- Uses the existing kernel report renderer (`data_studio.reporting.write_html_report`) to emit a self-contained HTML document with the result narrative, metrics, diagnostics, tables and provenance.
- File is saved with a suggested name based on the analysis kind and the user chooses the destination.

## Phase 0.6 — Workspace dashboards (done)

**Problem:** Dashboards could KPI/chart/table with slicers and saved views, but every time-intelligence question ("how are we tracking YoY?") needed manual exports, parameters were re-hardcoded per tile, and nobody could tell whether a dashboard was stale.

**What was built:**
- `data-studio date-table` (or the Dashboards → Model panel) generates a one-row-per-day **calendar dataset**; deriving the model afterwards yields grain-tagged date dimensions (`year`/`quarter`/`month`/`week`/`day`) and a `Calendar` hierarchy (year → quarter → month → day). Grain can also be edited per dimension in the model panel.
- Measures can wrap one aggregate with **time intelligence**: `running_total(sum(amount))`, `yoy(sum(amount))`, `yoy_pct(sum(amount))`, ordered by the grain-marked dimension the query groups by.
- Dashboards are interactive: chart marks and table rows **cross-filter** every tile (chips show what's active), hierarchy tiles **drill** year → quarter → month → day with breadcrumbs, and each tile has a **SQL** button showing dimensions, measures, elapsed time and compiled SQL.
- **What-if parameters** (number/text/choice/bool) are declared on the dashboard and referenced as `@name` in tile filters and row caps; viewers change them in the parameter bar and tiles re-query live. The builder has a parameter repeater and per-tile filter rows.
- Dashboards now carry a **schedule** (`manual`/`on_open`/`hourly`/`daily`) and an input fingerprint; `data-studio refresh --due`, the workspace timer and project open refresh due dashboards alongside saved analyses, with fresh/stale badges and an optional HTML artifact under `reports/`.

## Phase 0.7 — Phase 4: Excel-like editing and scripting (done)

**Problem:** the workspace could clean (pipeline) and chart (dashboards), but a working analyst still had to round-trip through Excel for ad-hoc derived columns, quick pivot cross-tabs, colour-coded cells and throwaway scripts — nothing a routine workbook replacement would accept.

**What was built:**
- **Formula bar** in Prepare — typed DSL expressions with live checks (`round(amount * 1.2, 2)`), target picker (replace a column or create one), apply-and-rerun, plus fill down, find &amp; replace, rename, cast, drop and copy-as-TSV quick actions.
- **Pivot view** — rows, an optional single pivot column and any number of value aggregations (sum, average, count, distinct count, min, max, median, stddev), server-side as one DuckDB conditional-aggregate query; copy as TSV or promote a single-value pivot to the pipeline.
- **Conditional formatting** per dataset, stored in the project (`formats/<dataset>.json`): data bars, heatmap and highlight (six operators, four colours); every grid freezes the header and first column. The desktop app gained **validation lists** (dropdown-only editing with rejected out-of-list values).
- **Python / R code step** — trusted local script executes on the table mid-pipeline with declared input/output Parquet paths, timeout kill and captured stderr/stdout on failure.
- **Script export** — `data-studio script .\project --format python|r [--output PATH]` writes a standalone reproducible pipeline (SQL steps verbatim with project-relative paths; code steps inlined).

## Later (optional)

- Semantic dashboards (KPI tiles + shared slicers)
- Project persistence (.pdw folders) instead of scratch-only
- Headless scheduling (Task Scheduler/cron)
- **Phase 5 — performance budget baseline established**: 10M-row DuckDB aggregation completes in ~0.2s (target <30s)
