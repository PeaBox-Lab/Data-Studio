# PBX Data Eng v0.35 - Release Gate & Repository Automation

## New
- Integrated Release Gate combining CI/CD status, workspace health, data quality and lineage readiness.
- Persistent release-gate run history.
- GitHub tag-driven release workflow template.
- Automated release packaging on `v*` tags after validation passes.
- Release evidence artifact upload.
- GitHub Release creation using `softprops/action-gh-release@v2`.
- Ctrl+K actions for Release Gate and Export Release Automation.

## Retained
All capabilities through v0.34 remain retained, including GitHub Actions/Azure Pipelines CI, contract tests, visual lineage and PNG/SVG export, contracts, validation, cleaning, analytics, ML, monitoring, deployment and scheduling.
