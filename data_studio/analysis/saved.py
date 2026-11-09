"""Saved analyses: persistence, invalidation and scheduled refresh.

A saved analysis is a small, versioned JSON document under ``analyses/`` that records an
analysis name, its parameters, the dataset/pipeline it ran against, a schedule, the typed
`AnalysisResult` and an input fingerprint. Nothing executable is stored: reopening a
project parses data only.

The fingerprint is derived from the dataset checksum, the serialised pipeline and the app
version. When a dataset is re-imported or a pipeline step changes, the fingerprint moves
and the saved result is reported as *stale* until it is refreshed. Schedules are evaluated
on demand (project open, a workspace timer, or ``data-studio refresh --due``), so there is
no background daemon to leak between processes.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..kernel.provenance import hash_parameters, utc_now_iso
from ..kernel.results import AnalysisResult
from ..project.manifest import safe_project_path
from ..reporting import write_html_report
from ..version import __version__
from .registry import AnalysisRegistry

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..project.store import Project

SCHEMA_VERSION = "1"
SAVED_FILENAME = "saved-analysis.json"
SCHEDULES = ("manual", "on_open", "hourly", "daily")
_SCHEDULE_INTERVAL = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
}
_SAVED_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_UNSAFE_ID = re.compile(r"[^a-z0-9]+")
_LOCK = threading.RLock()


class SavedAnalysisError(ValueError):
    """A saved analysis document or request was invalid."""


def slugify(value: str) -> str:
    """Turn a display name into a stable file id."""

    if not isinstance(value, str):
        raise TypeError("Saved analysis name must be a string.")
    candidate = _UNSAFE_ID.sub("-", value.strip().lower()).strip("-")
    candidate = candidate[:64].strip("-")
    if not candidate:
        raise SavedAnalysisError("Saved analysis name must contain letters or digits.")
    if not _SAVED_ID.match(candidate):
        raise SavedAnalysisError(f"Invalid saved analysis name {value!r}.")
    return candidate


def input_fingerprint(
    project: Project,
    dataset: str | None,
    use_pipeline: bool,
) -> str:
    """Cheap fingerprint of everything a saved result depends on."""

    target = dataset or project.manifest.active_dataset
    if not target:
        raise SavedAnalysisError("The project has no dataset to analyse.")
    try:
        entry = project.dataset_entry(target)
    except KeyError as exc:
        raise SavedAnalysisError(
            f"Dataset {target!r} is not part of this project."
        ) from exc
    payload: dict[str, Any] = {
        "dataset": target,
        "checksum_sha256": entry.checksum_sha256,
        "use_pipeline": bool(use_pipeline),
        "app_version": __version__,
    }
    if use_pipeline:
        payload["pipeline"] = project.load_pipeline().to_dict()
    return hash_parameters(payload)


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise SavedAnalysisError(f"Invalid timestamp {value!r}.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


@dataclass(frozen=True, slots=True)
class SavedAnalysis:
    """A persisted analysis definition plus its last typed result."""

    id: str
    title: str
    analysis: str
    params: Mapping[str, Any] = field(default_factory=dict)
    dataset: str | None = None
    use_pipeline: bool = True
    schedule: str = "manual"
    input_fingerprint: str = ""
    created_at: str = ""
    refreshed_at: str = ""
    report_path: str | None = None
    result: AnalysisResult | None = None

    def __post_init__(self) -> None:
        if not _SAVED_ID.match(self.id):
            raise SavedAnalysisError(f"Invalid saved analysis id {self.id!r}.")
        title = self.title.strip() if isinstance(self.title, str) else ""
        if not title:
            raise SavedAnalysisError("Saved analysis title must not be empty.")
        object.__setattr__(self, "title", title)
        if not isinstance(self.analysis, str) or not self.analysis.strip():
            raise SavedAnalysisError("Saved analysis name must not be empty.")
        object.__setattr__(self, "analysis", self.analysis.strip())

        params = dict(self.params)
        object.__setattr__(self, "params", params)
        if self.dataset is not None:
            dataset = str(self.dataset).strip()
            if not dataset:
                raise SavedAnalysisError("Saved analysis dataset must be a name or null.")
            object.__setattr__(self, "dataset", dataset)
        object.__setattr__(self, "use_pipeline", bool(self.use_pipeline))
        if self.schedule not in SCHEDULES:
            raise SavedAnalysisError(
                f"Schedule must be one of: {', '.join(SCHEDULES)}."
            )
        for name in ("input_fingerprint", "created_at", "refreshed_at"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise SavedAnalysisError(f"Saved analysis {name} must be a string.")
        if self.report_path is not None:
            relative = str(self.report_path).replace("\\", "/")
            if not relative.startswith("reports/"):
                raise SavedAnalysisError(
                    "Saved analysis reports must live under 'reports/'."
                )
            object.__setattr__(self, "report_path", relative)
        if self.result is not None and not isinstance(self.result, AnalysisResult):
            raise SavedAnalysisError("Saved analysis result must be an AnalysisResult.")

    def to_dict(self, *, include_result: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "id": self.id,
            "title": self.title,
            "analysis": self.analysis,
            "params": dict(self.params),
            "dataset": self.dataset,
            "use_pipeline": self.use_pipeline,
            "schedule": self.schedule,
            "input_fingerprint": self.input_fingerprint,
            "created_at": self.created_at,
            "refreshed_at": self.refreshed_at,
            "report_path": self.report_path,
        }
        if include_result:
            payload["result"] = self.result.to_dict() if self.result else None
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SavedAnalysis:
        if not isinstance(payload, Mapping):
            raise SavedAnalysisError("A saved analysis document must be an object.")
        raw_result = payload.get("result")
        result = None
        if raw_result is not None:
            if not isinstance(raw_result, Mapping):
                raise SavedAnalysisError("Saved analysis 'result' must be an object.")
            result = AnalysisResult.from_dict(raw_result)
        params = payload.get("params", {})
        if not isinstance(params, Mapping):
            raise SavedAnalysisError("Saved analysis 'params' must be an object.")
        return cls(
            id=str(payload.get("id", "")),
            title=str(payload.get("title", "")),
            analysis=str(payload.get("analysis", "")),
            params=dict(params),
            dataset=payload.get("dataset"),
            use_pipeline=bool(payload.get("use_pipeline", True)),
            schedule=str(payload.get("schedule", "manual")),
            input_fingerprint=str(payload.get("input_fingerprint") or ""),
            created_at=str(payload.get("created_at") or ""),
            refreshed_at=str(payload.get("refreshed_at") or ""),
            report_path=payload.get("report_path"),
            result=result,
        )


class SavedAnalysisStore:
    """Read and write saved analyses beneath one project folder."""

    def __init__(
        self,
        project: Project,
        *,
        registry: AnalysisRegistry | None = None,
    ) -> None:
        if registry is None:
            from . import DEFAULT_ANALYSIS_REGISTRY

            registry = DEFAULT_ANALYSIS_REGISTRY
        self.project = project
        self.registry = registry
        self.directory = project.root / "analyses"
        self.last_errors: list[str] = []

    # -- files -------------------------------------------------------------

    def _path(self, saved_id: str) -> Path:
        return safe_project_path(self.project.root, f"analyses/{saved_id}.json")

    def get(self, saved_id: str) -> SavedAnalysis:
        if not _SAVED_ID.match(str(saved_id)):
            raise SavedAnalysisError(f"Invalid saved analysis id {saved_id!r}.")
        path = self._path(saved_id)
        if not path.is_file():
            raise KeyError(f"Saved analysis {saved_id!r} does not exist.")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SavedAnalysisError(
                f"Saved analysis {saved_id!r} is not valid JSON: {exc}"
            ) from exc
        saved = SavedAnalysis.from_dict(payload)
        if saved.id != saved_id:
            raise SavedAnalysisError(
                f"Saved analysis file {path.name!r} declares id {saved.id!r}."
            )
        return saved

    def list(self) -> list[SavedAnalysis]:
        self.last_errors = []
        if not self.directory.is_dir():
            return []
        saved: list[SavedAnalysis] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                item = SavedAnalysis.from_dict(payload)
                if item.id != path.stem:
                    raise SavedAnalysisError(
                        f"File {path.name!r} declares id {item.id!r}."
                    )
                saved.append(item)
            except (
                OSError,
                ValueError,
                TypeError,
                KeyError,
            ) as exc:  # corrupted documents must not hide the rest
                self.last_errors.append(f"{path.name}: {exc}")
        return saved

    def remove(self, saved_id: str) -> None:
        path = self._path(saved_id)
        if not path.is_file():
            raise KeyError(f"Saved analysis {saved_id!r} does not exist.")
        path.unlink()

    # -- save and refresh --------------------------------------------------

    def save(
        self,
        *,
        name: str,
        analysis: str,
        params: Mapping[str, Any] | None = None,
        dataset: str | None = None,
        use_pipeline: bool = True,
        schedule: str = "manual",
        title: str | None = None,
        report_path: str | bool | None = None,
        overwrite: bool = False,
    ) -> SavedAnalysis:
        definition = self.registry.get(analysis)
        resolved = definition.validate(params or {})
        saved_id = slugify(name)
        existing = self._load_optional(saved_id)
        if existing is not None and not overwrite:
            saved_id = self._unique_id(saved_id)

        schedule = str(schedule)
        if schedule not in SCHEDULES:
            raise SavedAnalysisError(
                f"Schedule must be one of: {', '.join(SCHEDULES)}."
            )
        report = (
            self._resolve_report_path(report_path, saved_id)
            if report_path is not None
            else (existing.report_path if existing else None)
        )
        title_text = title or (existing.title if existing else definition.title)

        with _LOCK:
            fingerprint = input_fingerprint(self.project, dataset, use_pipeline)
            result = self._execute(analysis, resolved, dataset, use_pipeline)
            now = utc_now_iso()
            created = existing.created_at if existing else now
            saved = SavedAnalysis(
                id=saved_id,
                title=str(title_text),
                analysis=analysis,
                params=resolved,
                dataset=dataset,
                use_pipeline=use_pipeline,
                schedule=schedule,
                input_fingerprint=fingerprint,
                created_at=created,
                refreshed_at=now,
                report_path=report,
                result=result,
            )
            self._write(saved)
            if saved.report_path:
                self._write_report(saved)
            return saved

    def refresh(self, saved_id: str) -> SavedAnalysis:
        saved = self.get(saved_id)
        with _LOCK:
            fingerprint = input_fingerprint(
                self.project, saved.dataset, saved.use_pipeline
            )
            result = self._execute(
                saved.analysis, dict(saved.params), saved.dataset, saved.use_pipeline
            )
            refreshed = replace(
                saved,
                result=result,
                input_fingerprint=fingerprint,
                refreshed_at=utc_now_iso(),
            )
            self._write(refreshed)
            if refreshed.report_path:
                self._write_report(refreshed)
            return refreshed

    def refresh_due(
        self,
        *,
        now: datetime | None = None,
        only_on_open: bool = False,
    ) -> tuple[list[SavedAnalysis], list[dict[str, str]]]:
        """Refresh every saved analysis whose schedule and freshness require it."""

        moment = now or datetime.now(UTC)
        refreshed: list[SavedAnalysis] = []
        failures: list[dict[str, str]] = []
        for saved in self.list():
            if only_on_open and saved.schedule != "on_open":
                continue
            try:
                summary = self.describe(saved, now=moment)
            except (ValueError, KeyError) as exc:
                failures.append({"id": saved.id, "error": str(exc)})
                continue
            if saved.schedule == "manual":
                continue
            if not summary["due"]:
                continue
            try:
                refreshed.append(self.refresh(saved.id))
            except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
                failures.append({"id": saved.id, "error": str(exc)})
        return refreshed, failures

    def refresh_all(self) -> tuple[list[SavedAnalysis], list[dict[str, str]]]:
        refreshed: list[SavedAnalysis] = []
        failures: list[dict[str, str]] = []
        for saved in self.list():
            try:
                refreshed.append(self.refresh(saved.id))
            except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
                failures.append({"id": saved.id, "error": str(exc)})
        return refreshed, failures

    # -- presentation ------------------------------------------------------

    def describe(
        self,
        saved: SavedAnalysis,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        moment = now or datetime.now(UTC)
        status = "fresh"
        try:
            current = input_fingerprint(
                self.project, saved.dataset, saved.use_pipeline
            )
        except (SavedAnalysisError, ValueError, KeyError, RuntimeError):
            current = None
            status = "missing"
        else:
            if not saved.input_fingerprint or current != saved.input_fingerprint:
                status = "stale"
        due = self._due(saved, status, moment)
        payload = saved.to_dict(include_result=False)
        payload.update(
            {
                "status": status,
                "due": due,
                "next_refresh": self._next_refresh(saved, moment),
            }
        )
        return payload

    def summaries(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        moment = now or datetime.now(UTC)
        summaries = []
        for saved in self.list():
            summaries.append(self.describe(saved, now=moment))
        return summaries

    # -- internals ---------------------------------------------------------

    def _execute(
        self,
        analysis: str,
        params: Mapping[str, Any],
        dataset: str | None,
        use_pipeline: bool,
    ) -> AnalysisResult:
        context = self.project.analysis_context(
            dataset_id=dataset, use_pipeline=use_pipeline
        )
        return self.registry.run(analysis, context, params)

    def _write(self, saved: SavedAnalysis) -> None:
        _atomic_write_text(
            self._path(saved.id),
            json.dumps(
                saved.to_dict(),
                indent=2,
                ensure_ascii=False,
                default=str,
            )
            + "\n",
        )

    def _write_report(self, saved: SavedAnalysis) -> None:
        if not saved.result or not saved.report_path:
            return
        path = safe_project_path(self.project.root, saved.report_path)
        write_html_report(saved.result, path, title=saved.title)

    def _resolve_report_path(
        self, report_path: str | bool | None, saved_id: str
    ) -> str | None:
        if report_path in (None, False):
            return None
        if report_path is True:
            return f"reports/{saved_id}.html"
        if not isinstance(report_path, str) or not report_path.strip():
            raise SavedAnalysisError("report_path must be a non-empty string.")
        relative = report_path.strip().replace("\\", "/")
        if not relative.startswith("reports/"):
            raise SavedAnalysisError("Reports must live under 'reports/'.")
        safe_project_path(self.project.root, relative)
        return relative

    def _load_optional(self, saved_id: str) -> SavedAnalysis | None:
        try:
            return self.get(saved_id)
        except KeyError:
            return None

    def _unique_id(self, base: str) -> str:
        taken = {item.id for item in self.list()}
        counter = 2
        while True:
            suffix = f"-{counter}"
            candidate = f"{base[: 64 - len(suffix)]}{suffix}"
            if candidate not in taken:
                return candidate
            counter += 1

    def _due(self, saved: SavedAnalysis, status: str, now: datetime) -> bool:
        if saved.schedule == "manual":
            return False
        if status != "fresh":
            return True
        if saved.schedule == "on_open":
            return False
        interval = _SCHEDULE_INTERVAL[saved.schedule]
        try:
            last = _parse_time(saved.refreshed_at)
        except SavedAnalysisError:
            return True
        return now - last >= interval

    def _next_refresh(self, saved: SavedAnalysis, now: datetime) -> str | None:
        if saved.schedule in ("manual", "on_open"):
            return None
        interval = _SCHEDULE_INTERVAL[saved.schedule]
        try:
            last = _parse_time(saved.refreshed_at)
        except SavedAnalysisError:
            return None
        return (last + interval).astimezone(UTC).isoformat(timespec="seconds")


def report_digest(path: Path) -> str:
    """Stable digest of a written report, used by tests and future manifests."""

    return "sha256:" + sha256(path.read_bytes()).hexdigest()


__all__ = [
    "SAVED_FILENAME",
    "SCHEDULES",
    "SavedAnalysis",
    "SavedAnalysisError",
    "SavedAnalysisStore",
    "input_fingerprint",
    "report_digest",
    "slugify",
]
