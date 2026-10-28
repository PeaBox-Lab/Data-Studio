# Python Data Workbench v0.6 - Analytics Platform

## New: DuckDB + SQL Workspace
- Query every in-memory project dataset with DuckDB SQL
- Dedicated SQL editor and tabular result viewer
- Save SQL query results back into the project as reusable datasets
- SQL works beside pandas, NumPy, SciPy, scikit-learn and the Python Lab

## New: Dashboard Designer + Filters
- Dashboard designer for primary chart preferences and selected KPI cards
- Reusable dashboard filter definition
- Interactive filter values from the selected column
- KPI cards recalculate against the filtered DataFrame
- Existing dashboard distribution/profile and executive summary remain live

## New: Reusable KPI Definitions
- Define named KPIs against numeric columns
- Aggregations: sum, mean, median, min, max, count, and distinct count
- Formats: number, integer, percent and INR currency
- KPI definitions persist inside `.pdw` projects

## New: Reusable Pipelines
- Save current visual workflow history under a named pipeline
- Replay deterministic cleaning steps including missing-value fill, duplicates, type inference, text cleanup and outlier detection
- Parameterized historical steps that cannot safely be recreated are reported as skipped, rather than silently changed
- Pipeline definitions persist in project files

## Existing platform
Multi-dataset projects, visual cleaning, analytics, statistics, engineering/scientific tools, ML, model lifecycle, charts, dashboards, Python Lab, project save/open and exports remain included.

## Windows
Extract the ZIP and run `run_workbench_v0.6.bat`.

## Direction for v0.7
Move pipelines from history replay to explicit parameterized step objects; introduce dataset relationships and a semantic metrics layer; add Parquet/DuckDB larger-than-memory ingestion; replace pickle-based project sharing with a safer ZIP + JSON + Parquet format.
