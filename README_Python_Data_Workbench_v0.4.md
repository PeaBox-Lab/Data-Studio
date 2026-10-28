# Python Data Workbench v0.4

## New in v0.4

### Multi-dataset projects
- Keep multiple DataFrames in one workspace
- Switch active datasets from the project panel
- Import additional datasets without replacing existing work
- Remove datasets while retaining at least one active table
- Visual joins can use the active dataset and imported files

### Project persistence
- Save complete projects as `.pdw`
- Restore datasets, active table, workflow history, Python code, and trained model objects
- Export workflow history as JSON

### Model lifecycle
- Trained models are retained in the project
- Save model artifacts as `.pkl`
- Load model artifacts for batch prediction
- Validate required input features before prediction
- Return predictions directly to the active grid

### Retained capabilities
- Automatic classification/regression with preprocessing
- Cross-validated model comparison
- Line, bar, scatter, histogram, box, and heatmap charts
- Improved Python Lab with templates, run-selection, variable explorer, history, reset, and script export
- Cleaning, statistics, time-series, simulation, optimization, signal processing, and engineering operations

## Run on Windows

Extract the ZIP and double-click `run_workbench_v0.4.bat`.

## Security notes

- Open `.pdw` and `.pkl` files only from trusted sources because Python pickle files can execute code while loading.
- Python Lab executes trusted local scripts with the user's permissions.
- A production release should use a restricted worker process for Python execution and a safer project serialization format for untrusted sharing.
