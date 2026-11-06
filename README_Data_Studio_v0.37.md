# Data Studio v0.37

Data Studio is a Python-native desktop environment for data preparation, analytics, dashboards, statistics, engineering calculations, machine learning, automation, deployment governance, monitoring, and headless job execution.

## v0.37 focus

**Deployment Observability & Rollback Studio** adds scored DEV/TEST/PROD health snapshots, API-error and drift thresholds, rollback recommendations, persistent rollback history, and a governed rollback center. It also restores sixteen governance actions that were referenced but absent in the v0.36 source.

## Run

1. Run `run_data_studio_v0.37.bat` on Windows, or install `requirements_v0.37.txt` and run `python data_studio_v0.37.py`.
2. Use **Govern** for profiling, validation, contracts, CI evidence, release gates, promotion, observability, and rollback.
3. Press **Ctrl+K** to search all primary actions.

## Quality gate

Run `python data_studio_ci_v0.37.py --project .`. The gate compiles the application, core module, and headless runtime; checks required files and actions; and executes unit tests.

## Compatibility

v0.37 opens v0.36 `.pdw` projects and initializes the new governance, observability, and rollback fields with safe defaults. Project files continue to use Python pickle for compatibility; open only trusted project/model files.
