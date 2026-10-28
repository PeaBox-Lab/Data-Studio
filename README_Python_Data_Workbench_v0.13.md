# Python Data Workbench v0.13 - Statistics Studio

## New in v0.13

### Guided statistical tests
- Welch independent-samples t-test
- One-way ANOVA
- Chi-square test of independence
- Pearson and Spearman correlation
- Paired t-test
- Plain-language significance interpretation at alpha = 0.05

### Assumption checks
- Per-group sample size, mean and standard deviation
- Shapiro-Wilk normality test
- Brown-Forsythe-style Levene test using median centering for variance heterogeneity
- Shapiro sampling capped at 5,000 observations for practical execution

### Effect sizes
- Cohen's d for two independent groups
- Paired standardized mean difference
- Cramer's V for categorical association
- Conventional small/medium/large descriptive thresholds are identified as conventions, not universal rules

### Confidence intervals
- t-based 95% confidence interval for a population mean
- Mean and standard-error reporting

### Regression diagnostics
- Linear model equation
- R-squared
- RMSE and MAE
- Slope p-value
- Residual Shapiro-Wilk test
- Residual preview table

### Statistical reporting
- Statistics Studio analyses are retained during the project session and can be consolidated into a readable report
- Statistics results persist inside `.pdw` project files

## Retained
All v0.1-v0.12 platform functionality remains included.

## Run
Extract the ZIP and run `run_workbench_v0.13.bat`.

## Next milestone
v0.14 should deepen Engineering and Scientific Analytics with FFT/spectral analysis, configurable digital filters, peak analysis, numerical integration/root solving, curve fitting, reliability/Weibull analysis, SPC charts and a stronger Monte Carlo simulator.
