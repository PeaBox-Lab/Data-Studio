# Data Studio Process and Action Audit

| Process | Entry point | Evidence/state | v0.37 result |
|---|---|---|---|
| Data quality | Govern → Data quality profile | console profile, dataset state | Working |
| Remediation | Govern/Quick Actions → Cleaning plan | reviewable plan and pipeline history | Working |
| Validation | Govern → Validation rules | persisted rule set and results | Working |
| Data contract | Govern → Data contracts | persisted schema/rules | Working |
| Contract testing | Govern → Contract test automation | persisted PASS/FAIL run | Working |
| CI readiness | Govern/Quick Actions → CI/CD integration | persisted checks and status | Working |
| Release gate | Govern → Release gate | persisted checks and status | Working |
| Promotion | Govern → Environment promotion | blocked/promoted history | Working; PROD gated |
| Observability | Govern → Deployment observability | scored snapshots and reasons | New in v0.37 |
| Rollback | Govern → Rollback center | confirmation and history | New in v0.37 |
| Jobs | Automate / headless runtime | run records, retries, dependencies | Retained |
| API deployment | Automate | deployment metadata and package | Retained |
| Monitoring | Model/Quick Actions | baselines, drift runs, alerts | Retained |

The audit identified no intentional feature removals. v0.37 retains the v0.36 data, analytics, ML, dashboard, API, monitoring, scheduler, headless, contract, CI, release, and promotion state fields and adds backward-compatible fields for the new release.
