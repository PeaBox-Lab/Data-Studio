# Data Studio v0.38 Process Audit

| Process | v0.38 control |
|---|---|
| Project open | Migrate defaults, repair active dataset, validate structure, record migration evidence |
| Project save | Normalize to v0.38, validate structure, record diagnostic event, persist |
| Desktop jobs | Correlated start/finish diagnostic events and retained run history |
| Headless jobs | Shared migration, schedule policy, dependency policy, atomic writes, JSONL diagnostics |
| Retry | Shared deterministic attempt policy, including zero-delay retries |
| Long-running work | Background-task manager with progress and cancellation foundation |
| Governance | v0.37 quality, contract, CI, gate, promotion, observability, and rollback processes retained |

No existing functional area was intentionally removed.
