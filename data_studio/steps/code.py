"""Trusted local code step.

A code step is the escape hatch for transformations that do not fit the declarative
registry: custom Python or R logic running on the table as it flows through the
pipeline. The step is *executed*, once per pipeline run, against the input table
currently in flight — it is not a sandbox and carries the same trust as the Python
Lab: it runs as the current user, with the current environment.

Contract for the script:

* ``DATA_STUDIO_INPUT_PARQUET`` — path to a Parquet snapshot of the input table.
* ``DATA_STUDIO_OUTPUT_PARQUET`` — path the script must write (single table) to.
* The script's working directory is a fresh scratch folder; it must not read or
  write elsewhere. Exit codes other than 0 fail the pipeline with the captured
  output; exceeding ``timeout`` seconds kills the process.
"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..engine.duckdb_engine import DuckDBTable
from ..io.writers import write_table
from .registry import StepContext, StepDefinition, StepError, StepParam

_SESSION_DIRS: list[Path] = []
_SESSION_OUTPUTS: list[str] = []


def session_outputs() -> list[str]:
    """Scratch output paths written by code steps, in execution order."""

    return list(_SESSION_OUTPUTS)


def _session_dir() -> Path:
    path = Path(tempfile.gettempdir()) / "data-studio-code" / uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    _SESSION_DIRS.append(path)
    return path


@atexit.register
def _cleanup_session_dirs() -> None:
    for path in _SESSION_DIRS:
        shutil.rmtree(path, ignore_errors=True)


def _interpreter(language: str) -> str:
    if language == "python":
        return sys.executable
    return "Rscript"


def _code(
    context: StepContext,
    table: DuckDBTable,
    params: Mapping[str, Any],
) -> DuckDBTable:
    language = params["language"]
    code = params["code"].strip()
    if not code:
        raise StepError("Code step needs non-empty 'code'.")
    timeout = int(params["timeout"])
    if not 1 <= timeout <= 3600:
        raise StepError("Code step 'timeout' must be between 1 and 3600 seconds.")
    if len(code) > 64 * 1024:
        raise StepError("Code step is limited to 64 KB of source.")

    workdir = _session_dir()
    input_path = workdir / "input.parquet"
    output_path = workdir / "output.parquet"
    try:
        write_table(context.engine, table, input_path, overwrite=True)
    except Exception as exc:
        raise StepError(f"Code step could not write its input: {exc}") from exc

    suffix = ".py" if language == "python" else ".R"
    script_path = workdir / f"step{suffix}"
    script_path.write_text(code, encoding="utf-8")
    _SESSION_OUTPUTS.append(str(output_path))

    env = os.environ.copy()
    env["DATA_STUDIO_INPUT_PARQUET"] = str(input_path)
    env["DATA_STUDIO_OUTPUT_PARQUET"] = str(output_path)
    try:
        completed = subprocess.run(
            [_interpreter(language), str(script_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=workdir,
            env=env,
        )
    except FileNotFoundError as exc:
        missing = "Rscript" if language == "r" else "python"
        raise StepError(
            f"Code step needs {missing} on PATH; install it or choose another language."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise StepError(
            f"Code step timed out after {timeout} second(s)."
        ) from exc

    if completed.returncode != 0:
        output = (completed.stdout or "") + (completed.stderr or "")
        tail = output.strip()[-800:] or f"exit code {completed.returncode}"
        raise StepError(f"Code step failed (exit {completed.returncode}): {tail}")
    if not output_path.is_file():
        raise StepError(
            "Code step did not write an output table; the script must write "
            "DATA_STUDIO_OUTPUT_PARQUET as Parquet."
        )
    try:
        return context.engine.from_parquet(
            output_path, name=f"code_{uuid4().hex[:12]}"
        )
    except Exception as exc:
        raise StepError(f"Code step output is not a readable Parquet file: {exc}") from exc


STEPS = (
    StepDefinition(
        name="code",
        title="Python / R code",
        category="code",
        description=(
            "Run trusted local Python or R on the table. The script reads the input "
            "from DATA_STUDIO_INPUT_PARQUET and must write one Parquet table to "
            "DATA_STUDIO_OUTPUT_PARQUET. Executes at pipeline run time as the "
            "current user; no sandbox."
        ),
        params=(
            StepParam(
                "language",
                "enum",
                default="python",
                choices=("python", "r"),
                description="Script language. R requires Rscript on PATH.",
            ),
            StepParam(
                "code",
                "code",
                description=(
                    "Script source (max 64 KB). Reads from DATA_STUDIO_INPUT_PARQUET "
                    "and writes the result to DATA_STUDIO_OUTPUT_PARQUET."
                ),
            ),
            StepParam(
                "timeout",
                "integer",
                required=False,
                default=60,
                description="Kill the script after this many seconds (1-3600).",
            ),
        ),
        compile=_code,
    ),
)


__all__ = ["STEPS", "session_outputs"]
