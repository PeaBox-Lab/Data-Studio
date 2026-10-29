# PBX Data Eng v0.35 User Guide

## Release Gate
Open Ctrl+K and select **Release gate**. The gate executes CI/contract checks and adds workspace-health, active-data-quality and lineage-readiness signals. FAIL is blocking; WARN is visible but non-blocking.

## GitHub Release Automation
Select **Export release automation** and choose your repository folder. Commit `.github/workflows/pbx-release.yml`. Push a tag such as `v0.35` to trigger validation and, when successful, release packaging and GitHub Release creation.
