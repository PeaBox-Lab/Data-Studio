# Python Data Workbench v0.21 - Automation Orchestrator

## New in v0.21

- Run every due job with one `--run-due` command.
- Continuous orchestration mode with configurable polling and bounded test cycles.
- Hourly, daily, weekly, and monthly next-run calculation stored in each project.
- Job dependencies through `depends_on`; downstream jobs run only after dependencies succeed.
- Retry policies through `max_retries` and `retry_delay_seconds`.
- Dependency-aware ordering when multiple jobs are due in the same orchestration cycle.
- Blocked-state reporting for failed, missing, circular, or unresolved dependencies.
- Expanded job listing with due state, last run, next run, dependencies, and retry policy.
- Desktop job creation now captures retry count and optional dependency job IDs.
- Job Manager displays retry and dependency settings.

## Desktop run

Run `run_workbench_v0.21.bat`.

## Run one job

```text
python workbench_headless_v0.21.py --project workbench_project.pdw --job-id job_0001
```

## Run all due jobs once

```text
python workbench_headless_v0.21.py --project workbench_project.pdw --run-due
```

## Start continuous orchestration

```text
python workbench_headless_v0.21.py --project workbench_project.pdw --daemon --poll-seconds 60
```

For a controlled validation run:

```text
python workbench_headless_v0.21.py --project workbench_project.pdw --daemon --poll-seconds 1 --max-cycles 1
```

## Job policy fields

- `depends_on`: list of job IDs that must have a successful last status.
- `max_retries`: number of retry attempts after the first failed attempt.
- `retry_delay_seconds`: delay between attempts.
- `last_run_utc`: completion timestamp from the latest run.
- `next_run_utc`: calculated next schedule timestamp.

## Operational behavior

The orchestrator executes due jobs sequentially inside a protected project update. Generated datasets, exports, monitoring records, job history, retry details, and next-run state are persisted atomically. Each run is also appended to the project JSONL operations log.

## Compatibility

v0.21 loads projects created by earlier releases. Existing scheduled jobs receive default dependency and retry behavior when those fields are absent.
