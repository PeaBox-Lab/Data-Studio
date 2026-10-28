# Python Data Workbench v0.17 - Model Monitoring and Drift

## New in v0.17

### Monitoring baseline
- Capture feature distributions for a registered model version
- Numeric decile bins, proportions, mean, standard deviation and missing rate
- Categorical top values, other bucket, proportions, cardinality and missing rate
- Baseline tied to model version, dataset, target and features

### Data drift
- Population Stability Index for numeric and categorical features
- Stable: PSI below 0.10
- Warning: PSI from 0.10 to below 0.25
- Critical: PSI at or above 0.25
- Missing-rate change monitoring
- Missing features treated as critical
- Persistent drift runs and alerts

### Model performance monitoring
- Classification: accuracy, balanced accuracy, weighted F1 and ROC-AUC when available
- Regression: R-squared, MAE and RMSE
- Comparison with model-registry baseline metrics
- Warning when primary performance decreases by more than 0.05
- Critical when primary performance decreases by more than 0.10
- Persistent performance history

### Monitoring dashboard and report
- PSI bar chart with warning and critical thresholds
- Feature-level alert list
- Consolidated monitoring and drift report
- Baselines, runs, alerts and thresholds persist in project files

## Run
Extract and run `run_workbench_v0.17.bat`.

## Notes
PSI thresholds are practical defaults and should be adjusted for the risk, volume, seasonality and governance requirements of each model. Monitoring detects change; it does not by itself prove that retraining is necessary.
