# Python Data Workbench v0.14 - Engineering and Scientific Analytics Studio

## New in v0.14

### Signal and spectral analysis
- Real FFT amplitude spectrum
- Periodogram metadata
- Sampling-rate and Nyquist reporting
- Dominant-frequency detection
- Peak detection with prominence and minimum spacing

### Digital filtering
- Butterworth low-pass, high-pass and band-pass filters
- Configurable sampling rate, cutoff frequencies and filter order
- Zero-phase SOS filtering through SciPy
- Filtered signal returned to the active grid

### Numerical computation
- Numerical integration with error estimate
- Brent bracketed root solving
- Safe expression namespace for common NumPy mathematical functions
- Parameter sweeps with best-response detection

### Curve fitting
- Linear, quadratic and exponential models
- Parameter estimates, R-squared and RMSE
- Fitted values returned to the active grid

### Reliability and quality engineering
- Two-parameter Weibull fit with B10 and median life
- Individuals control chart limits using moving-range sigma estimation
- Out-of-control point detection
- Cp and Cpk process capability calculation

### Monte Carlo Pro
- Normal, uniform, triangular and lognormal input distributions
- User-defined output expression
- 1,000 to 2,000,000 simulations
- Mean, standard deviation, P01, P05, P25, P50, P75, P95 and P99
- Probability of output below zero

### Reporting
- Engineering analyses are retained in the project and consolidated into an Engineering Report

## Run
Extract and run `run_workbench_v0.14.bat`.

## Next milestone
v0.15 should implement AutoML Studio with problem detection, preprocessing recipes, candidate model comparison, configurable cross-validation, tuning, classification/regression metrics, feature importance and reusable trained pipelines.
