# Python Data Workbench v0.8 - Semantic Analytics Platform

v0.8 implements the next roadmap milestone: a reusable data model and semantic layer on top of the hybrid pandas, DuckDB and optional PySpark engine architecture.

## New in v0.8

### Dataset relationships
- Relationship Manager across project datasets
- From/to dataset and key selection
- Cardinality labels: one-to-one, one-to-many and many-to-one
- Key validation showing uniqueness, duplicates and matching distinct values
- Automatic relationship suggestions when two datasets share a column name and at least 50% of the smaller key domain overlaps
- Many-to-many candidates are identified but not automatically activated

### Calculated columns
- Reusable calculated-column definitions
- pandas expression engine, for example `output / pressure`
- Calculated definitions persist with the project

### Semantic measures
- Reusable named measures stored independently of charts
- Aggregations: sum, mean, median, min, max, count and distinct count
- Optional equality filter context
- Measure evaluator
- Semantic measures automatically become dashboard KPI cards for their dataset

### Model overview
- Inspect all datasets
- Inspect relationships and cardinality
- Inspect calculated columns
- Inspect semantic measures

### Retained v0.7 engine architecture
- pandas for editable GUI and Python/scikit-learn workflows
- DuckDB for local analytical SQL and larger local workloads
- optional PySpark for distributed workloads
- Engine selector, Spark SQL, cache, explain plan and guarded collection

## Run
Extract the ZIP and double-click `run_workbench_v0.8.bat`.

## Next architectural milestone
v0.9 should replace history-based workflow replay with explicit parameterized pipeline-step objects, dependency tracking, preview-per-step, reproducible execution against another dataset, and JSON pipeline import/export.
