"""Reproducible 5M-row CSV benchmark for the Phase 1 kernel.

Generates a messy CSV with DuckDB, imports it into a folder project, runs a
clean/join pipeline, exports Parquet and reports timings as JSON.

    python scripts/benchmark_5m.py --rows 5000000

The file is written to the system temporary directory by default. Pass
``--output-dir`` to keep the project and artifacts somewhere else. Existing
projects are only replaced when ``--reset`` is given.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
from pathlib import Path

from data_studio.engine import DuckDBEngine
from data_studio.pipeline import make_step
from data_studio.project import Project


def build_source(raw: Path, rows: int) -> float:
    started = time.perf_counter()
    engine = DuckDBEngine()
    try:
        engine.execute(
            f"""
            COPY (
                SELECT
                    i AS order_id,
                    (i % 500) AS customer_id,
                    CASE i % 5
                        WHEN 0 THEN ' North '
                        WHEN 1 THEN 'north'
                        WHEN 2 THEN 'SOUTH'
                        WHEN 3 THEN 'South '
                        ELSE 'west'
                    END AS region,
                    CASE WHEN i % 997 = 0 THEN NULL
                         ELSE round(random() * 100 + 10, 2) END AS amount,
                    strftime(DATE '2024-01-01' + INTERVAL (i % 365) DAY, '%Y-%m-%d') AS order_date,
                    CASE i % 3
                        WHEN 0 THEN 'new'
                        WHEN 1 THEN 'shipped'
                        ELSE 'cancelled'
                    END AS status,
                    1 AS revision
                FROM range(1, {int(rows) + 1}) AS t(i)
            ) TO '{raw.as_posix()}' (FORMAT CSV, HEADER true)
            """
        )
    finally:
        engine.close()
    return time.perf_counter() - started


def build_pipeline(project: Project) -> None:
    pipeline = project.load_pipeline()
    pipeline = pipeline.add_step(make_step("trim_text", {"columns": ["region"]}))
    pipeline = pipeline.add_step(
        make_step("normalize_text", {"columns": ["region"], "case": "title"})
    )
    pipeline = pipeline.add_step(
        make_step("cast", {"column": "amount", "dtype": "float", "try_cast": True})
    )
    pipeline = pipeline.add_step(
        make_step("fill_missing", {"columns": ["amount"], "method": "mean"})
    )
    pipeline = pipeline.add_step(
        make_step(
            "date_parse",
            {"column": "order_date", "format": "%Y-%m-%d", "output": "order_ts"},
        )
    )
    pipeline = pipeline.add_step(make_step("deduplicate", {"columns": ["order_id"]}))
    pipeline = pipeline.add_step(
        make_step("filter", {"expression": "status != 'cancelled'"})
    )
    pipeline = pipeline.add_step(
        make_step(
            "join",
            {
                "dataset": "regions",
                "how": "left",
                "left_on": ["region"],
                "right_on": ["region"],
                "cardinality": "many_to_one",
            },
        )
    )
    project.save_pipeline(pipeline)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=5_000_000)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "data_studio_benchmark",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete an existing project in the output directory.",
    )
    args = parser.parse_args()

    base = args.output_dir.resolve()
    base.mkdir(parents=True, exist_ok=True)
    raw = base / "orders.csv"
    lookup_path = base / "regions.csv"
    export_path = base / "clean.parquet"
    project_dir = base / "Project"
    if project_dir.exists():
        if not args.reset:
            parser.error(f"{project_dir} exists; pass --reset to replace it.")
        shutil.rmtree(project_dir)

    generate_seconds = build_source(raw, args.rows)
    lookup_path.write_text("region,zone\nNorth,N\nSouth,S\nWest,W\n", encoding="utf-8")

    project = Project.create(project_dir, f"{args.rows:,}-row benchmark")
    try:
        started = time.perf_counter()
        entry = project.add_dataset(raw, dataset_id="orders")
        import_seconds = time.perf_counter() - started
        project.add_dataset(lookup_path, dataset_id="regions")
        build_pipeline(project)

        started = time.perf_counter()
        result = project.run_pipeline()
        run_seconds = time.perf_counter() - started

        started = time.perf_counter()
        project.export(result, export_path)
        export_seconds = time.perf_counter() - started

        started = time.perf_counter()
        cached = project.run_pipeline()
        cached_seconds = time.perf_counter() - started

        summary = {
            "rows_generated": args.rows,
            "rows_imported": entry.rows,
            "rows_result": result.rows,
            "columns": len(result.schema),
            "cache_hits": cached.cache_hits,
            "generate_seconds": round(generate_seconds, 3),
            "import_seconds": round(import_seconds, 3),
            "run_seconds": round(run_seconds, 3),
            "export_seconds": round(export_seconds, 3),
            "cached_rerun_seconds": round(cached_seconds, 3),
            "csv_mb": round(raw.stat().st_size / 1e6, 1),
            "parquet_mb": round(export_path.stat().st_size / 1e6, 1),
            "project": str(project.root),
        }
    finally:
        project.close()

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
