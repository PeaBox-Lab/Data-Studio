# Python Data Workbench v0.7 - Hybrid Analytics Platform

## Engine decision

v0.7 deliberately uses a hybrid backend rather than replacing pandas with PySpark.

- **pandas** remains the editable in-process DataFrame for responsive desktop grids, Python Lab compatibility, scikit-learn, SciPy, and Matplotlib.
- **DuckDB** is the default scale-up engine for SQL, joins, aggregations, and Parquet on one workstation.
- **PySpark** is optional for genuinely distributed workloads and organizations that already have Spark or Databricks infrastructure.

PySpark is newer in distributed execution, but it is not a universal replacement for pandas. Starting a JVM/Spark session and collecting results adds overhead, while a desktop grid cannot display billions of rows. The productive design is to process remotely/distributed and materialize only bounded result sets for visual editing.

## New in v0.7

- Compute-engine selector: Auto, pandas, DuckDB, PySpark
- Automatic recommendation based on active dataset size
- Engine status and in-memory-size report
- Optional local SparkSession creation
- Convert active pandas DataFrame to Spark DataFrame
- Register Spark SQL temporary views
- Spark SQL dialog and bounded result preview
- Spark DataFrame cache action
- Spark and DuckDB execution-plan inspection
- Guarded collection from Spark back to pandas
- 250,000-row collection safety boundary
- Separate optional Spark installer so normal desktop users do not download or start Spark unnecessarily

## Run

Run `run_workbench_v0.7.bat` for the standard pandas + DuckDB platform.

To enable PySpark, first install Java 17, set `JAVA_HOME`, and run `install_spark_optional_v0.7.bat`.

## Recommended deployment

- Individual analyst laptop: pandas + DuckDB
- Multi-million-row local Parquet/CSV analytics: DuckDB
- Existing Databricks/Spark estate or distributed terabyte-scale jobs: PySpark connector/cluster execution
- GUI preview/editing: bounded pandas results

## Security and scale

Do not collect unbounded Spark data into the desktop process. Production Spark support should submit jobs to a configured remote Spark Connect or Databricks endpoint, enforce row limits, and return only aggregated or sampled results to the GUI.
