# Python Data Workbench v0.19 - Scheduler and Jobs

## New in v0.19
- Create persistent jobs for pipelines, model batch scoring, dataset export and drift monitoring
- Manual, hourly, daily, weekly and monthly schedule definitions
- Run jobs immediately from the GUI
- Job Manager with run, enable/disable and delete controls
- Run history with status, duration, output details and errors
- Dataset outputs from pipeline and model-scoring jobs are retained inside the project
- Export jobs write CSV, Excel or Parquet to configured paths
- Monitoring jobs execute saved drift baselines and append monitoring runs
- Scheduler report consolidates job definitions and latest execution history
- Export headless job contracts and Windows Task Scheduler registration scripts

## Important boundary
v0.19 stores schedules and can export Task Scheduler contracts, but the desktop process itself is not a persistent background daemon. v0.20 should implement the complete headless automation runtime that loads a project and executes a job from the command line without opening the GUI.

## Run
Extract and run `run_workbench_v0.19.bat`.
