# Python Data Workbench v0.15 - AutoML Studio

## AutoML workflow
1. Select a target column.
2. Automatically detect classification or regression.
3. Build numeric and categorical preprocessing.
4. Cross-validate candidate models.
5. Review the leaderboard.
6. Tune the best model.
7. Evaluate an independent 20% holdout.
8. Explain predictions using permutation importance.
9. Export a model bundle and readable schema.

## Candidate models

### Classification
- Dummy baseline
- Logistic Regression
- Random Forest
- Extra Trees
- Histogram Gradient Boosting

### Regression
- Dummy baseline
- Linear Regression
- Ridge Regression
- Random Forest
- Extra Trees
- Histogram Gradient Boosting

## Preprocessing
- Median imputation for numeric data
- Standard scaling for numeric data
- Most-frequent imputation for categorical data
- One-hot encoding with unknown-category handling
- Preprocessing and estimator saved together as a scikit-learn Pipeline

## Evaluation
- Stratified cross-validation for classification
- K-fold cross-validation for regression
- Classification: accuracy, weighted F1 and ROC-AUC
- Regression: R-squared, MAE and RMSE
- Independent 20% holdout evaluation
- Classification report and confusion matrix
- Regression residual output

## Tuning and explainability
- Randomized hyperparameter search for supported candidate models
- Permutation importance on up to 5,000 rows
- Saved experiment metadata, leaderboard, metrics and feature importance

## Deployment artifact
- Pickled full preprocessing/model pipeline
- Separate JSON schema and metadata sidecar
- Feature list, target, problem type, metrics and tuning details

## Security
Only load `.pkl` or `.automl.pkl` files from trusted sources. Pickle is not safe for untrusted sharing.

## Run
Extract and run `run_workbench_v0.15.bat`.

## Next milestone
v0.16 should add deeper model explainability and lifecycle management: SHAP integration, ROC/PR curves, calibration, learning curves, experiment comparison, model registry, versioning and prediction monitoring.
