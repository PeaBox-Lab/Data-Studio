# Python Data Workbench v0.16 - ML Explainability and Lifecycle

## New in v0.16

### Classification diagnostics
- Confusion matrix visualization
- Stored classification report
- Binary probability calibration/reliability curve
- Learning curves using configured cross-validation

### Regression diagnostics
- Residual versus predicted plot
- Residual distribution
- R-squared, MAE, residual mean and residual standard deviation
- Learning curves for regression

### Explainability
- Existing global permutation importance retained
- New local perturbation-based sensitivity explanation at source-feature level
- Shows original value, reference value and prediction impact
- Explicitly identified as sensitivity analysis rather than causal explanation

### Model lifecycle
- Versioned model-registry entries generated whenever a best AutoML model is trained
- Active/inactive model state by dataset and target
- Registry records model name, problem type, features and metrics
- Fitted preprocessing/model pipeline retained for each version
- Prediction history records model version, dataset, target, row count and output column
- Registry and history persist inside project files

### Retained AutoML
- Automatic classification/regression detection
- Mixed-type preprocessing
- 2-10 fold cross-validation
- Candidate model leaderboards
- Best-model holdout evaluation
- ROC curves
- Permutation feature importance

## Run
Extract and run `run_workbench_v0.16.bat`.

## Next milestone
v0.17 should focus on enterprise connections: SQL Server, PostgreSQL, MySQL, SQLite/ODBC, REST APIs, SharePoint/OneDrive and cloud/object storage, with a connection manager and secure credential strategy.
