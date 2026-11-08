"""Source-format detection and CSV dialect sniffing.

Real exports are messy: semicolon-delimited European CSVs, tab-separated dumps,
Windows-1252 text, headers that are almost but not quite data. This module makes the
best safe guess and returns explicit options that are recorded with the dataset, so a
re-import never depends on guesswork.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

_BOM_ENCODINGS = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)
_DELIMITERS = ",;\t|:"
_SAMPLE_BYTES = 64 * 1024


class SourceFormat(StrEnum):
    """Supported input and output formats."""

    PARQUET = "parquet"
    CSV = "csv"
    JSON = "json"
    EXCEL = "xlsx"
    DUCKDB = "duckdb"
    UNKNOWN = "unknown"

    @property
    def suffix(self) -> str:
        return _SUFFIX_BY_FORMAT[self]


_SUFFIX_BY_FORMAT = {
    SourceFormat.PARQUET: ".parquet",
    SourceFormat.CSV: ".csv",
    SourceFormat.JSON: ".json",
    SourceFormat.EXCEL: ".xlsx",
    SourceFormat.DUCKDB: ".duckdb",
    SourceFormat.UNKNOWN: "",
}

_FORMAT_BY_SUFFIX = {
    ".parquet": SourceFormat.PARQUET,
    ".pq": SourceFormat.PARQUET,
    ".csv": SourceFormat.CSV,
    ".tsv": SourceFormat.CSV,
    ".txt": SourceFormat.CSV,
    ".json": SourceFormat.JSON,
    ".jsonl": SourceFormat.JSON,
    ".ndjson": SourceFormat.JSON,
    ".xlsx": SourceFormat.EXCEL,
    ".xlsm": SourceFormat.EXCEL,
    ".xls": SourceFormat.EXCEL,
    ".duckdb": SourceFormat.DUCKDB,
    ".ddb": SourceFormat.DUCKDB,
    ".db": SourceFormat.DUCKDB,
}


def detect_format(path: str | Path) -> SourceFormat:
    """Infer a source format from the file suffix."""

    suffix = Path(path).suffix.casefold()
    return _FORMAT_BY_SUFFIX.get(suffix, SourceFormat.UNKNOWN)


def normalise_format(
    value: str | SourceFormat | None,
    path: str | Path | None = None,
) -> SourceFormat:
    """Resolve an explicit or inferred format, rejecting unsupported values."""

    if isinstance(value, SourceFormat):
        return value
    if value:
        try:
            return SourceFormat(str(value).casefold())
        except ValueError as exc:
            raise ValueError(f"Unsupported source format: {value!r}.") from exc
    if path is None:
        return SourceFormat.UNKNOWN
    return detect_format(path)


@dataclass(frozen=True, slots=True)
class CsvOptions:
    """Explicit CSV dialect options recorded with a dataset."""

    delimiter: str = ","
    quote: str = '"'
    header: bool = True
    encoding: str = "utf-8-sig"
    sample_size: int = 20_480

    def __post_init__(self) -> None:
        if not self.delimiter or len(self.delimiter) > 1:
            raise ValueError("CSV delimiter must be a single character.")
        if not self.quote or len(self.quote) > 1:
            raise ValueError("CSV quote character must be a single character.")
        if not isinstance(self.header, bool):
            raise TypeError("CSV header must be a boolean.")
        if self.sample_size < 0:
            raise ValueError("CSV sample_size must be >= 0.")
        if not self.encoding.strip():
            raise ValueError("CSV encoding must not be empty.")

    def to_dict(self) -> dict[str, object]:
        return {
            "delimiter": self.delimiter,
            "quote": self.quote,
            "header": self.header,
            "encoding": self.encoding,
            "sample_size": self.sample_size,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object] | None) -> CsvOptions:
        if not payload:
            return cls()
        return cls(
            delimiter=str(payload.get("delimiter", ",")),
            quote=str(payload.get("quote", '"')),
            header=bool(payload.get("header", True)),
            encoding=str(payload.get("encoding", "utf-8-sig")),
            sample_size=int(payload.get("sample_size", 20_480)),
        )


def _decode_sample(raw: bytes) -> tuple[str, str]:
    for bom, encoding in _BOM_ENCODINGS:
        if raw.startswith(bom):
            return raw.decode(encoding, errors="replace"), encoding
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1"


def sniff_csv(path: str | Path, *, sample_bytes: int = _SAMPLE_BYTES) -> CsvOptions:
    """Guess the dialect of a CSV file without reading the whole file."""

    resolved = Path(path)
    if not resolved.is_file():
        raise FileNotFoundError(f"CSV file not found: {resolved}")
    with resolved.open("rb") as handle:
        raw = handle.read(sample_bytes)
    text, encoding = _decode_sample(raw)
    if "\x00" in text:
        raise ValueError(f"{resolved.name!r} looks binary, not CSV text.")

    sample = text[:sample_bytes]
    lines = [line for line in sample.splitlines() if line.strip()][:50]
    if not lines:
        return CsvOptions(encoding=encoding)

    delimiter = ","
    quote = '"'
    try:
        dialect = csv.Sniffer().sniff("\n".join(lines), delimiters=_DELIMITERS)
        delimiter = dialect.delimiter
        quote = dialect.quotechar or '"'
    except csv.Error:
        delimiter = max(_DELIMITERS, key=lambda candidate: lines[0].count(candidate))

    header = True
    try:
        header = bool(csv.Sniffer().has_header("\n".join(lines)))
    except csv.Error:
        first = next(csv.reader(io.StringIO(lines[0]), delimiter=delimiter))
        header = bool(first) and not any(_looks_numeric(cell) for cell in first)

    return CsvOptions(
        delimiter=delimiter,
        quote=quote,
        header=header,
        encoding=encoding,
    )


def _looks_numeric(value: str) -> bool:
    candidate = value.strip().replace(",", "").replace("%", "")
    if not candidate:
        return False
    try:
        float(candidate)
    except ValueError:
        return False
    return True
