# Data Studio v0.38 User Guide

## Existing workflows

All v0.37 workflows remain available through Data, Transform, Analyze, Visualize, Model, Automate, Govern, Advanced, Ctrl+K Quick Actions, and the headless runner.

## Opening older projects

Open a trusted v0.36 or v0.37 `.pdw` project normally. Data Studio initializes missing state, repairs an invalid active-dataset pointer when possible, validates the result, and upgrades the in-memory project to v0.38. Save the project to persist the upgraded state.

## Diagnostics Center

Open **Govern → Diagnostics Center** or search for it with Ctrl+K. The center shows recent event time, level, component, action, message, and correlation ID. Desktop project saves, opens, and job runs produce structured events.

Headless execution writes an append-only file beside the project using the suffix `.diagnostics.jsonl`.

## Background-task foundation

v0.38 includes a bounded task manager with progress and cancellation APIs. Existing dialogs behave as before; future releases will move expensive operations onto this foundation incrementally.

## Validation

Run `python data_studio_ci_v0.38.py --project .` to compile every module, verify required files and UI actions, and run the complete test suite.

## Security

Only open trusted project and model files. Pickle compatibility remains in v0.38 and is scheduled for replacement by a manifest-based package in v0.39.
