# Python Data Workbench v0.20 - Headless Automation Runtime

## New in v0.20

- Complete command-line runtime that opens a saved `.pdw` project without starting the GUI.
- Executes saved pipeline, model batch scoring, dataset export, and monitoring drift jobs.
- Persists generated datasets, monitoring runs, alerts, job history, counters, and last-run status.
- Atomic project saves and a project lock prevent partially written or overlapping job updates.
- Structured JSON results, meaningful process exit codes, and optional `last_run.json` output.
- Append-only `<project>.pdw.jobs.jsonl` operational log for scheduler troubleshooting.
- `--list-jobs`, `--job-id`, `--job-file`, `--save-as`, `--no-save`, and `--lock-timeout` options.
- Exported runners now contain the runtime, job contract, Windows and shell launchers, and Task Scheduler registration.
- Desktop project saves now identify the project as version `0.20`.

## Desktop run

Run `run_workbench_v0.20.bat`.

## Command-line examples

List jobs:

```text
python workbench_headless_v0.20.py --project workbench_project.pdw --list-jobs
```

Run a saved job:

```text
python workbench_headless_v0.20.py --project workbench_project.pdw --job-id job_0001
```

Run an exported contract and write a result file:

```text
python workbench_headless_v0.20.py --project workbench_project.pdw --job-file job.json --json-output last_run.json
```

## Exit codes

- `0`: job completed successfully, or jobs were listed.
- `1`: the selected job ran but failed.
- `2`: the runtime could not load, lock, validate, or process the request.

## Scheduler use

Save the desktop project before using **Export job runner**. The exported folder contains the v0.20 runtime and launch files. If the project is moved later, update the project path in `runner.json`.

## Compatibility

v0.20 loads projects created by earlier releases. A project is upgraded to version `0.20` when the desktop app or headless runtime saves it.
