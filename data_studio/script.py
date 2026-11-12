"""Generate a reproducible script for a project's pipeline.

Most pipeline steps compile to SQL, so the transformation is one self-contained
DuckDB query. Code steps (trusted Python/R) do not fit SQL, so the exporter splits
the pipeline around them:

1. Every prefix of SQL steps before a code step is emitted as its own stage that
   materialises a temporary Parquet file which later code steps (and the final
   query) read.
2. The code step's source is inlined as-is with ``DATA_STUDIO_INPUT_PARQUET`` and
   ``DATA_STUDIO_OUTPUT_PARQUET`` pointed at the staged files. Python code runs
   inline; R code is written to a temporary file and executed via ``Rscript``.
3. The final SQL — rewritten to read the staged outputs instead of session scratch
   files — produces the requested output.

Dataset paths are written relative to the project root. Python export supports both
Python and R code steps; the R exporter cannot translate Python code steps (use
``--format python``). Code steps are re-executed when the script runs; that is the
point of them.
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .steps.code import session_outputs
from .version import __version__

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .project.store import Project

SCRIPT_FORMATS = ("python", "r")


class ScriptExportError(ValueError):
    """A script could not be generated for the requested pipeline."""


def _quote_forward(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _relative_dataset_paths(project: Project, sql: str) -> str:
    """Rewrite absolute dataset paths inside the SQL to project-relative ones."""

    rewritten = sql
    for dataset_id in project.dataset_ids():
        entry = project.dataset_entry(dataset_id)
        absolute = (Path(project.root) / entry.path).resolve()
        variants = {
            _quote_forward(str(absolute).replace("\\", "/")),
            _quote_forward(
                os.path.join(str(project.root), entry.path).replace("\\", "/")
            ),
        }
        for variant in variants:
            rewritten = rewritten.replace(
                variant, _quote_forward(entry.path.replace("\\", "/"))
            )
    return rewritten


def _provenance_lines(project: Project, steps: tuple) -> list[str]:
    lines = [
        f"Project: {project.name}",
        f"Generated: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"Data Studio version: {__version__}",
        f"Steps executed: {len(steps)}",
    ]
    lines.extend(
        f"  {index + 1}. {step.operation} (step id {step.id})"
        for index, step in enumerate(steps)
    )
    return lines


def _stages(project: Project) -> tuple[str, list[dict]]:
    """Run the pipeline and collect code-step stages.

    Returns ``(final_sql, stages)`` with one entry per code step, in execution
    order. Each stage records the step, its ``prefix_sql`` (``None`` when the
    code step is the first step) and the base dataset path when applicable.
    """

    pipeline = project.load_pipeline()
    enabled = tuple(pipeline.enabled_steps)
    if not enabled:
        raise ScriptExportError(
            "The pipeline has no steps; add steps before exporting a script."
        )
    code_indices = [
        index for index, step in enumerate(enabled) if step.operation == "code"
    ]
    base_dataset = pipeline.dataset or project.manifest.active_dataset
    if base_dataset is None:
        raise ScriptExportError("The project has no active dataset.")
    base_entry = project.dataset_entry(base_dataset)

    before_full_run = len(session_outputs())
    full = project.run_pipeline(count_rows=False)
    sql = full.table.sql

    full_scratch = session_outputs()[before_full_run:]
    if len(full_scratch) < len(code_indices):
        raise ScriptExportError(
            "Code step outputs could not be located for script export."
        )

    stages: list[dict] = []
    for position, index in enumerate(code_indices):
        step = enabled[index]
        scratch = full_scratch[position]
        prefix_sql: str | None = None
        base_path: str | None = None
        if index == 0:
            base_path = base_entry.path.replace("\\", "/")
        else:
            truncated = replace(pipeline, steps=enabled[:index])
            before = len(session_outputs())
            prefix = project.run_pipeline(pipeline=truncated, count_rows=False)
            prefix_sql = prefix.table.sql
            # The prefix re-executes earlier code steps; point them at the stage
            # files the script produces earlier.
            for stage_position, scratch_path in enumerate(
                session_outputs()[before:], start=1
            ):
                prefix_sql = prefix_sql.replace(
                    _quote_forward(scratch_path.replace("\\", "/")),
                    f"'stage_{stage_position}.parquet'",
                )
            prefix_sql = _relative_dataset_paths(project, prefix_sql)
        stages.append(
            {
                "index": position + 1,
                "step": step,
                "prefix_sql": prefix_sql,
                "base_path": base_path,
                "scratch": scratch,
            }
        )
        sql = sql.replace(
            _quote_forward(scratch.replace("\\", "/")), f"'stage_{position + 1}.parquet'"
        )
    sql = _relative_dataset_paths(project, sql)
    return sql, stages


def _python_script(sql: str, stages: list[dict], provenance: list[str]) -> str:
    header = "\n".join(f"#   {line}" for line in provenance)
    parts = [
        '''"""Reproducible pipeline generated by data-studio.

{header}

Dataset paths are relative to the project root. Code steps are inlined; run
from anywhere.

    python pipeline_export.py [PROJECT_DIR] [OUTPUT_PARQUET]

Requires: pip install duckdb pandas
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import duckdb
    import pandas as pd  # noqa: F401  (inlined steps may use it)
except ImportError as exc:  # pragma: no cover - environment guard
    raise SystemExit(
        "This script needs duckdb and pandas: pip install duckdb pandas"
    ) from exc

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
OUTPUT = (
    Path(sys.argv[2]).resolve()
    if len(sys.argv) > 2
    else ROOT / "exports" / "pipeline-output.parquet"
)
WORK = Path(tempfile.mkdtemp(prefix="data-studio-"))
os.chdir(ROOT)  # dataset paths in SQL are relative to the project root

con = duckdb.connect()
'''.replace("{header}", header)
    ]
    for stage in stages:
        step = stage["step"]
        number = stage["index"]
        language = step.params.get("language", "python")
        prefix = stage["prefix_sql"]
        parts.append(f"\n# -- Step {number}: code (id {step.id})\n")
        parts.append(f'STAGE_{number} = WORK / "stage_{number}.parquet"\n')
        if prefix is not None:
            sql_literal = repr(prefix)
            parts.append(f'INPUT_{number} = WORK / "input_{number}.parquet"\n')
            parts.append("con.execute(\n")
            parts.append(f'    "COPY (" + {sql_literal} + ") TO "\n')
            parts.append(
                f"    + \"'\" + str(INPUT_{number}) + \"' (FORMAT PARQUET)\"\n"
            )
            parts.append(")\n")
            parts.append(
                f"os.environ['DATA_STUDIO_INPUT_PARQUET'] = str(INPUT_{number})\n"
            )
        else:
            parts.append(
                f"os.environ['DATA_STUDIO_INPUT_PARQUET'] = "
                f"str(ROOT / {stage['base_path']!r})\n"
            )
        parts.append(
            f"os.environ['DATA_STUDIO_OUTPUT_PARQUET'] = str(STAGE_{number})\n"
        )
        parts.append("CODE = " + repr(step.params["code"]) + "\n")
        if language == "python":
            parts.append(
                "exec(CODE, {'__name__': '__main__'})\n"
            )
        else:
            parts.append(
                f"script_path = WORK / 'step_{number}.R'\n"
                f"script_path.write_text(CODE, encoding='utf-8')\n"
                f"subprocess.run(['Rscript', str(script_path)], check=True,\n"
                f"               cwd=WORK, env=os.environ.copy())\n"
            )
    parts.append(
        f"\n# -- Final steps\n"
        f"final_sql = {sql!r}\n"
    )
    for stage in stages:
        number = stage["index"]
        parts.append(
            f"new_{number} = str(WORK / 'stage_{number}.parquet').replace('\\\\', '/')\n"
f"final_sql = final_sql.replace('stage_{number}.parquet', new_{number})\n"
        )
    parts.append(
        "con.execute(\n"
        "    \"COPY (\" + final_sql + \") TO '\" + str(OUTPUT) + \"' (FORMAT PARQUET)\"\n"
        ")\n"
        "print(f\"Wrote {OUTPUT}\")\n"
    )
    return "".join(parts)


def _r_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def _r_script(sql: str, stages: list[dict], provenance: list[str]) -> str:
    header = "\n".join(f"#   {line}" for line in provenance)
    parts = [
        f'''# Reproducible pipeline generated by data-studio.
{header}
#
# Dataset paths are relative to the project root. Code steps are inlined; run
# from anywhere.
#
#   Rscript pipeline_export.R [PROJECT_DIR] [OUTPUT_PARQUET]
#
# Requires: install.packages("duckdb") and install.packages("DBI")

args <- commandArgs(trailingOnly = TRUE)
root <- if (length(args) >= 1) args[[1]] else getwd()
output <- if (length(args) >= 2) args[[2]] else
  file.path(root, "exports", "pipeline-output.parquet")
work <- tempfile("data-studio-")
dir.create(work)
setwd(normalizePath(root))  # dataset paths in SQL are relative to the project root

suppressPackageStartupMessages(library(duckdb))
suppressPackageStartupMessages(library(DBI))
con <- dbConnect(duckdb())
'''
    ]
    for stage in stages:
        step = stage["step"]
        number = stage["index"]
        parts.append(f"\n# -- Step {number}: code (id {step.id})\n")
        parts.append(f'stage_{number} <- file.path(work, "stage_{number}.parquet")\n')
        if stage["prefix_sql"] is not None:
            parts.append(
                f'input_{number} <- file.path(work, "input_{number}.parquet")\n'
                f'dbExecute(con, paste0("COPY (", {_r_string(stage["prefix_sql"])}, '
                f'") TO \'", input_{number}, "\' (FORMAT PARQUET)"))\n'
                f"Sys.setenv(DATA_STUDIO_INPUT_PARQUET = input_{number})\n"
            )
        else:
            parts.append(
                f"Sys.setenv(DATA_STUDIO_INPUT_PARQUET = file.path(\n"
                f"  root, {_r_string(stage['base_path'])}))\n"
            )
        parts.append(
            f"Sys.setenv(DATA_STUDIO_OUTPUT_PARQUET = stage_{number})\n"
        )
        parts.append(step.params["code"] + "\n")
    parts.append("\n# -- Final steps\n")
    parts.append(f"final_sql <- {_r_string(sql)}\n")
    for stage in stages:
        number = stage["index"]
        parts.append(
            f'final_sql <- gsub("stage_{number}.parquet", '
            f'gsub("\\\\\\\\", "/", file.path(work, "stage_{number}.parquet")), '
            f"final_sql, fixed = TRUE)\n"
        )
    parts.append(
        "dbExecute(con, paste0(\"COPY (\", final_sql,\n"
        "  \") TO '\", output, \"' (FORMAT PARQUET)\"))\n"
        "dbDisconnect(con)\n"
        "cat(sprintf(\"Wrote %s\\n\", output))\n"
    )
    return "".join(parts)


def generate_script(
    project: Project,
    *,
    fmt: str = "python",
) -> str:
    """Return the reproducible script (Python or R) for the active pipeline."""

    fmt = str(fmt).strip().lower()
    if fmt not in SCRIPT_FORMATS:
        raise ScriptExportError(
            f"Script format must be one of: {', '.join(SCRIPT_FORMATS)}."
        )
    sql, stages = _stages(project)
    provenance = _provenance_lines(
        project, tuple(project.load_pipeline().enabled_steps)
    )
    if fmt == "r":
        for stage in stages:
            language = stage["step"].params.get("language", "python")
            if language != "r":
                raise ScriptExportError(
                    "R export cannot translate Python code steps; "
                    "use --format python instead."
                )
        return _r_script(sql, stages, provenance)
    return _python_script(sql, stages, provenance)
