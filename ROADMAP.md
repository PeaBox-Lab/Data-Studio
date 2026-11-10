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

## Later (optional)

- Semantic dashboards (KPI tiles + shared slicers)
- Project persistence (.pdw folders) instead of scratch-only
- Headless scheduling (Task Scheduler/cron)
