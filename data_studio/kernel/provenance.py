"""Reproducibility metadata attached to every analysis result."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from ..version import __version__


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with an offset."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def hash_parameters(parameters: Mapping[str, Any]) -> str:
    """Stable hash of analysis parameters, used to key cached results."""
    encoded = json.dumps(
        dict(parameters),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return "sha256:" + sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where a result came from and how to reproduce it."""

    dataset: str
    dataset_fingerprint: str = ""
    step_ids: tuple[str, ...] = ()
    engine: str = "unknown"
    app_version: str = __version__
    created_at: str = field(default_factory=utc_now_iso)
    parameters_hash: str | None = None

    def __post_init__(self) -> None:
        dataset = self.dataset.strip()
        if not dataset:
            raise ValueError("Provenance dataset must not be empty.")
        object.__setattr__(self, "dataset", dataset)

        steps = tuple(self.step_ids)
        if any(not isinstance(step, str) or not step.strip() for step in steps):
            raise ValueError("Provenance step_ids must be non-empty strings.")
        object.__setattr__(self, "step_ids", steps)

        engine = self.engine.strip()
        if not engine:
            raise ValueError("Provenance engine must not be empty.")
        object.__setattr__(self, "engine", engine)

        if not isinstance(self.created_at, str):
            raise TypeError("Provenance created_at must be an ISO-8601 string.")
        try:
            parsed = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"Invalid provenance timestamp: {self.created_at!r}.") from exc
        if parsed.tzinfo is None:
            raise ValueError("Provenance created_at must include a timezone.")
        object.__setattr__(self, "created_at", parsed.isoformat(timespec="seconds"))

        if self.parameters_hash is not None and not self.parameters_hash.startswith("sha256:"):
            raise ValueError("parameters_hash must use the sha256: prefix.")

    @classmethod
    def create(
        cls,
        dataset: str,
        dataset_fingerprint: str = "",
        step_ids: Sequence[str] = (),
        engine: str = "unknown",
        parameters: Mapping[str, Any] | None = None,
    ) -> Provenance:
        return cls(
            dataset=dataset,
            dataset_fingerprint=dataset_fingerprint,
            step_ids=tuple(step_ids),
            engine=engine,
            parameters_hash=hash_parameters(parameters) if parameters is not None else None,
        )

    def with_steps(self, step_ids: Sequence[str]) -> Provenance:
        return Provenance(
            dataset=self.dataset,
            dataset_fingerprint=self.dataset_fingerprint,
            step_ids=tuple(step_ids),
            engine=self.engine,
            app_version=self.app_version,
            created_at=self.created_at,
            parameters_hash=self.parameters_hash,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "dataset_fingerprint": self.dataset_fingerprint,
            "step_ids": list(self.step_ids),
            "engine": self.engine,
            "app_version": self.app_version,
            "created_at": self.created_at,
            "parameters_hash": self.parameters_hash,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Provenance:
        return cls(
            dataset=str(payload.get("dataset", "")),
            dataset_fingerprint=str(payload.get("dataset_fingerprint", "")),
            step_ids=tuple(str(step) for step in payload.get("step_ids", [])),
            engine=str(payload.get("engine", "unknown")),
            app_version=str(payload.get("app_version", __version__)),
            created_at=str(payload.get("created_at", utc_now_iso())),
            parameters_hash=payload.get("parameters_hash"),
        )
