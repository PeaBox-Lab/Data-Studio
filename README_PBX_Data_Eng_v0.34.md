# PBX Data Eng v0.34 - CI/CD Integration Studio

## New
- In-app CI/CD Integration Studio with local quality-gate execution.
- GitHub Actions workflow generation with Python 3.11/3.12 matrix, compile gate, CI runner and evidence artifact upload.
- Azure Pipelines YAML generation with Python setup, compile gate, CI runner and evidence publishing.
- Standalone `pbx_ci_v0.34.py` runner with non-zero failure exit code for pipeline gating.
- CI/CD profiles and run history persisted in project files.
- CI/CD template export from Ctrl+K.
- Contract-test automation from v0.33 incorporated into the quality-gate workflow.

## Retained
All functionality through v0.33 remains, including contract tests, visual lineage and PNG/SVG export, data contracts, validation, cleaning, analytics, ML, monitoring, APIs, scheduling and headless execution.
