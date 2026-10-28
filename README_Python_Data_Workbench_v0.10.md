# Python Data Workbench v0.10 - Large Data Analytics Platform

v0.10 implements the large-data roadmap milestone while retaining v0.1-v0.9 capabilities.

## New in v0.10
- Attach CSV and Parquet as lazy DuckDB sources without loading them into pandas
- Lazy-source manager with format, file size, path, engine and materialization status
- Bounded materialization to an editable pandas DataFrame
- Direct DuckDB SQL over CSV/Parquet files
- Schema, row-count and sampled source profiling
- Default GUI preview limit: 10,000 rows
- Guarded materialization ceiling: 500,000 rows
- Engine-routed pipeline compiler for filters, deduplication, missing-row removal and calculated-column steps
- Pipeline SQL preview and output row count without collecting the full result

## Architecture
Use pandas for interactive editing and Python/scikit-learn work, DuckDB for larger-than-memory local analytics, and optional PySpark for genuinely distributed workloads.

## Run
Extract the ZIP and run `run_workbench_v0.10.bat`.

## Next milestone
v0.11 should focus on Dashboard Designer 2.0: multiple independent visual tiles, layout persistence, slicers, cross-filtering, drill-down, conditional formatting and exportable dashboard definitions.
