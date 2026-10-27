# Python Data Workbench v0.3

## New in v0.3

### Machine learning
- Automatic classification or regression selection
- Mixed numeric/categorical preprocessing with imputation, scaling and one-hot encoding
- Random Forest training with a 75/25 holdout
- Classification accuracy/report or regression R-squared/MAE
- Predictions returned to the active grid
- Cross-validated regression comparison across Linear Regression, Ridge, Random Forest and Extra Trees

### Charts
- Line, bar, scatter, histogram and box charts
- Optional grouping by categorical columns
- Correlation heatmap
- PNG, SVG and PDF export
- Charts use the active DataFrame and refresh after transformations

### Python Lab
- Run all or selected code
- Templates for cleaning, grouping, regression, charts and machine learning
- Variable explorer
- Execution history
- Reset the active DataFrame
- Save scripts as `.py`
- Optional return of the updated `df` to the grid
- Full traceback in the console

## Run

Double-click `run_workbench_v0.3.bat`. The launcher creates a local `.venv` and installs dependencies.

## Security

The Python Lab is intended for trusted local scripts. Production deployment should run scripts in an isolated worker process with time, memory and filesystem restrictions.
