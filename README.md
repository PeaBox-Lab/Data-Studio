# Data Studio

A local-first analytics workbench for cleaning, joining, analyzing, visualizing and reporting on
tabular data. The long-term goal is one platform for the work people currently split across Excel,
R, SAS, Tableau and Power BI.

**Current status:** Phase 0 (foundation) and Phase 1 (kernel MVP) are implemented. The new
package can import CSV/Excel/JSON/Parquet/DuckDB files, run an ordered, validated pipeline
with incremental reruns and undo, export results, and reopen a project reproducibly. v0.38
remains a frozen desktop prototype kept runnable as a reference. See
[`REVIEW_AND_ROADMAP.md`](REVIEW_AND_ROADMAP.md) for the review, gap analysis and phased plan.

## Documentation

| Document | Contents |
|---|---|
| [`REVIEW_AND_ROADMAP.md`](REVIEW_AND_ROADMAP.md) | Measured review of v0.38, gap analysis, target architecture and phased plan. |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Architecture decision records: why the web app, kernel, engines and project format. |
| [`docs/PROJECT_FORMAT.md`](docs/PROJECT_FORMAT.md) | The versioned folder project format, paths, checksums and integrity rules. |
| [`USER_GUIDE_v0.38.md`](USER_GUIDE_v0.38.md) | Guide to the frozen desktop prototype. |

## The two codebases

| Path | Purpose |
|---|---|
| `data_studio_v0.38.py` + `data_studio_*_v0_38.py` | The legacy desktop prototype. Kept runnable as a reference. Do not add features. |
| `data_studio/` | The new package: kernel contracts, project format, API, CLI. |

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## Run the new stack

```powershell
data-studio doctor
data-studio init .\examples\demo --name "Demo"
data-studio verify-project .\examples\demo
data-studio serve --project .\examples\demo --port 8765
```

Then open <http://127.0.0.1:8765>. Interactive API docs are at `/docs`.

### Import, clean, run and export headlessly

```powershell
data-studio add-dataset .\examples\demo .\data\sales.csv --id sales
data-studio add-step .\examples\demo trim_text --params '{"columns":["region"]}'
data-studio add-step .\examples\demo cast --params '{"column":"amount","dtype":"float","try_cast":true}'
data-studio add-step .\examples\demo drop_missing --params '{"columns":["amount"]}'
data-studio run .\examples\demo
data-studio export .\examples\demo .\data\clean.parquet
```

The same project opens in the browser workspace, where datasets, schemas, previews,
pipeline steps and exports are editable. Opening a project after a restart reproduces the
same results because datasets, schemas, checksums and the pipeline are all on disk.

## Run the legacy prototype

```powershell
.\.venv\Scripts\python.exe -m pip install PySide6 matplotlib scipy scikit-learn openpyxl
.\.venv\Scripts\python.exe data_studio_v0.38.py
```

## Test

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check data_studio tests scripts
```

Phase 1 includes a reproducible scale benchmark:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_5m.py --rows 5000000 --reset
```

A 5M-row messy CSV run on the development laptop imported in 1.0 s, ran the 8-step
clean/join pipeline in 0.4 s, exported a 26 MB Parquet file in 1.4 s, and re-ran with
8/8 cache hits in 0.4 s.

## Architecture in one paragraph

All surfaces (web, CLI, scheduler, tests) call the same kernel. A `TableSchema` describes columns and
roles; an `AnalysisResult` carries typed tables, metrics, diagnostics, narrative and charts; a project
is a folder containing `project.json`, Parquet datasets and JSON definitions — never pickle. The
execution engine is DuckDB over Arrow, with pandas kept for small-data compatibility.
