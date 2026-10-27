# Python Data Workbench v0.2

A native PySide6 data workbench with one live pandas DataFrame shared by visual tools and Python Lab.

## New in v0.2

- Visual left join using a shared key
- Pivot-table and group-analysis operations
- Hourly resampling and rolling time metrics
- Welch two-sample t-test
- Linear regression with predicted values
- Three-sigma quality-control flags
- Feature engineering
- Random Forest regression
- K-means clustering
- Isolation Forest anomaly detection
- Moving-average forecasting
- Savitzky-Golay signal smoothing
- Quadratic response optimization
- Monte Carlo simulation

## Run on Windows

Double-click `run_workbench_v0.2.bat`. Python must be available through the `py` launcher. Dependencies are installed into a local `.venv`.

## Security

Python Lab runs trusted local code with the user's permissions. A production build should move execution to a restricted worker process with time, memory, and filesystem limits.
