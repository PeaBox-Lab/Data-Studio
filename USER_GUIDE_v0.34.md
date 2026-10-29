# PBX Data Eng v0.34 User Guide

## In the desktop app
Open Ctrl+K and choose **CI/CD integration**. Run the local quality gate first. Then use **Export CI/CD** to write GitHub Actions and Azure Pipelines templates into a repository folder.

## Command line
`python pbx_ci_v0.34.py --report ci-report.json`

For a saved project use `--project <project-file>`. Failed checks return process exit code 1, allowing the step to block a build.

## GitHub Actions
Place the generated workflow under `.github/workflows/`. The included template validates Python 3.11 and 3.12 and uploads `ci-report.json` as pipeline evidence.

## Azure Pipelines
Place `azure-pipelines.yml` at repository root and connect it to an Azure DevOps pipeline. The report is published even when the gate fails.
