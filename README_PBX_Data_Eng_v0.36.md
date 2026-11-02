# PBX Data Eng v0.36 - Environment Promotion & Deployment Governance

## New
- DEV -> TEST -> PROD promotion model.
- Promotion gate evaluates contract-suite availability, data quality and workspace health.
- PROD promotion additionally requires the latest CI/CD run and contract-test run to pass.
- Protected PROD environment foundation.
- Persistent promotion history and environment configuration in project files.
- GitHub Actions environment-promotion workflow export with manual TEST/PROD selection.
- Deployment evidence artifact generation and GitHub Environment integration.
- Ctrl+K actions for Environment Promotion and Export Deployment Automation.

## Retained
All capabilities through v0.35 remain retained, including CI/CD, release gates, contract testing, visual lineage/export, data contracts, profiling, cleaning, analytics, ML, monitoring, APIs, jobs and headless execution.
