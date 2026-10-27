# Python Data Workbench v0.1

A native PySide6 desktop prototype combining an editable DataFrame grid with visual data preparation, analysis, statistics, data science, engineering tools, and a shared Python Lab.

## Run on Windows

Double-click `run_workbench.bat`. It creates a local virtual environment and installs dependencies without requiring npm.

## Run on Linux/macOS

```sh
./run_workbench.sh
```

## Current working features

- Editable pandas DataFrame grid with sorting and global search
- CSV, Excel, JSON, and Parquet import
- Excel, CSV, and Parquet export
- Missing-value filling
- Duplicate removal
- Basic type inference and text cleanup
- Descriptive profiling and correlation output
- IQR outlier flagging
- Matrix summary
- Python Lab sharing the live `df`
- History snapshots and Undo
- Visual project, quality, and workflow panels

## Important security note

The Python Lab executes local Python code with the permissions of the logged-in user. Only run trusted scripts. A production release should execute code in an isolated worker process with resource limits and project-scoped file access.
