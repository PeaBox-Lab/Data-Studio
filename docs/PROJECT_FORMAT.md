# Project format

**Format version:** `1.0`
**Manifest filename:** `project.json`
**Pipeline filename:** `steps/pipeline.json`
**Implementation:** [`data_studio/project/manifest.py`](../data_studio/project/manifest.py),
[`data_studio/project/store.py`](../data_studio/project/store.py)

A Data Studio project is a directory, not a database and not a pickle. Everything needed
to open, verify and audit the project lives in plain files with explicit versions.

```
MyProject/
  project.json          # the manifest: identity, settings, dataset index, checksums
  datasets/             # Parquet (preferred), CSV, JSON or Excel data files
    sales.parquet
    customers.parquet
  steps/                # ordered pipeline definition: pipeline.json
    pipeline.json
  analyses/             # stored AnalysisResult documents (Phase 2)
  dashboards/           # dashboard specifications (Phase 3)
  models/               # model artifacts and metadata (Phase 5)
```

The `steps/`, `analyses/`, `dashboards/` and `models/` directories are created with every
project so later phases can add files without changing the layout.

## Design guarantees

1. **No code execution on open.** The format never uses `pickle`, `eval` or importable
   objects.
2. **Paths cannot escape.** Every path in the manifest is a canonical, relative POSIX path
   that resolves inside the project directory.
3. **Integrity is checkable.** Every dataset has a SHA-256 checksum.
4. **Saves are atomic.** The manifest is written to a temporary file, flushed, `fsync`ed
   and moved into place with `os.replace`.
5. **Versions are explicit.** The manifest declares its format version; readers reject
   formats they do not understand.
6. **Pipelines are data, not code.** `steps/pipeline.json` stores operation names and
   typed parameters. It never contains SQL, Python or importable objects. Unknown
   operations and invalid parameters fail at run time with the step id in the error.

## `project.json`

Example:

```json
{
  "manifest_version": "1.0",
  "project_id": "2f1c9b8e-4a4d-4a8a-9f0e-8b7d6c5a4e3f",
  "name": "Q4 Sales Review",
  "app_version": "0.39.0.dev0",
  "created_at": "2026-11-08T09:30:00+00:00",
  "updated_at": "2026-11-08T10:15:00+00:00",
  "datasets": [
    {
      "id": "sales",
      "path": "datasets/sales.parquet",
      "format": "parquet",
      "schema": {
        "columns": [
          {
            "name": "order_id",
            "dtype": "string",
            "nullable": false,
            "role": "identifier",
            "unit": null,
            "description": null,
            "metadata": {}
          },
          {
            "name": "amount",
            "dtype": "float",
            "nullable": true,
            "role": "measure",
            "unit": "USD",
            "description": "Net order value",
            "metadata": {}
          }
        ],
        "primary_key": ["order_id"]
      },
      "checksum_sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
      "created_at": "2026-11-08T09:31:00+00:00",
      "rows": 12043,
      "source": "q4_sales_extract.xlsx"
    }
  ],
  "active_dataset": "sales",
  "directories": ["datasets", "steps", "analyses", "dashboards", "models"],
  "settings": {
    "preview_rows": 500
  }
}
```

### Top-level fields

| Field | Type | Required | Rules |
|---|---|---|---|
| `manifest_version` | string | yes | Must equal the current format version (`"1.0"`). Unknown versions are rejected. |
| `project_id` | string | yes | A UUID. Stable for the life of the project. |
| `name` | string | yes | Non-empty after trimming. |
| `app_version` | string | yes | Non-empty; the application version that last wrote the manifest. |
| `created_at` | string | yes | ISO-8601 timestamp with a timezone. |
| `updated_at` | string | yes | ISO-8601 timestamp with a timezone. |
| `datasets` | array | yes | Dataset entries. May be empty. Ids and paths must be unique. |
| `active_dataset` | string or null | yes | Must reference a dataset id, or be `null` when there are no datasets. |
| `directories` | array | yes | Unique relative paths; created on `init`. |
| `settings` | object | no | Free-form application settings. Does not affect integrity. |

All timestamps include a timezone. Naive timestamps are rejected; the loader does not
guess the machine's local zone.

## `steps/pipeline.json`

A project has one ordered pipeline. It is a single JSON document so a run is always
reproducible from one file. Each step is an operation name plus JSON parameters; the
kernel validates parameters and compiles SQL, so a project file can never inject SQL.

Example:

```json
{
  "schema_version": "1.0",
  "name": "Clean Q4 sales",
  "dataset": "sales",
  "steps": [
    {
      "id": "trim_text",
      "operation": "trim_text",
      "params": { "columns": ["region"] },
      "title": "Trim region",
      "enabled": true
    },
    {
      "id": "join_regions",
      "operation": "join",
      "params": {
        "dataset": "regions",
        "how": "left",
        "left_on": ["region"],
        "right_on": ["region"],
        "cardinality": "many_to_one"
      },
      "title": null,
      "enabled": true
    }
  ]
}
```

| Field | Type | Required | Rules |
|---|---|---|---|
| `schema_version` | string | yes | Must equal `"1.0"`; unknown versions are rejected. |
| `name` | string | no | 1-200 characters; defaults to `"Pipeline"`. |
| `dataset` | string or null | no | Base dataset id; when null the manifest's `active_dataset` is used. |
| `steps` | array | yes | Ordered, may be empty. Step ids must be unique. |
| `steps[].id` | string | yes | `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`. |
| `steps[].operation` | string | yes | A name from the step registry (`trim_text`, `filter`, `join`, ...). |
| `steps[].params` | object | no | JSON-serialisable parameters validated by the step definition. |
| `steps[].title` | string or null | no | Human-readable label; empty becomes null. |
| `steps[].enabled` | boolean | no | Defaults to `true`; disabled steps are skipped without being removed. |

Execution rules:

- Steps run in order. Each enabled step compiles a new lazy query over the previous one.
- A step fingerprint covers the operation, parameters, input fingerprint and any dataset
  it joins or unions. Changing a step invalidates that step and every later step; earlier
  steps stay cached.
- Undo removes the last step and saves the document. Removing a step reruns from that
  point on the next run.
- A failed step raises an error naming the step id and operation; the project is not
  modified.
- The pipeline document is written atomically (temporary file + `os.replace`).

## Dataset entries

| Field | Type | Required | Rules |
|---|---|---|---|
| `id` | string | yes | `^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`; unique in the project. |
| `path` | string | yes | Canonical relative POSIX path ending in the format's suffix; unique. |
| `format` | string | yes | One of `parquet`, `csv`, `json`, `xlsx`. |
| `schema` | object | yes | A serialized `TableSchema` (see below). |
| `checksum_sha256` | string | yes | Exactly 64 lowercase hexadecimal characters; SHA-256 of the file bytes. |
| `created_at` | string | no | ISO-8601 timestamp with a timezone; defaults to now. |
| `rows` | integer or null | no | Non-negative. A hint; the file is authoritative. |
| `source` | string or null | no | Original filename or description; trimmed, empty becomes `null`. |

Format suffixes:

| `format` | Required suffix |
|---|---|
| `parquet` | `.parquet` |
| `csv` | `.csv` |
| `json` | `.json` |
| `xlsx` | `.xlsx` |

## Schema documents

A schema is stored inline with each dataset so a file can be interpreted without the
original source. The same document is used by the kernel, API and results.

```json
{
  "columns": [
    {
      "name": "region",
      "dtype": "category",
      "nullable": false,
      "role": "dimension",
      "unit": null,
      "description": "Sales region",
      "metadata": {}
    }
  ],
  "primary_key": []
}
```

`dtype` is a logical type, not a storage type:

`boolean`, `integer`, `float`, `decimal`, `string`, `date`, `datetime`, `time`,
`duration`, `category`, `binary`, `json`, `unknown`.

`role` is the intended analytical use:

`dimension`, `measure`, `identifier`, `time`, `geography`, `text`, `ignore`.

Rules:

- Column names are non-empty and unique case-insensitively.
- If `role` is omitted, it is inferred from `dtype`.
- A primary key column must exist in the schema and must be non-nullable.
- Primary key names are unique.
- Schemas are immutable value objects and have a stable fingerprint.

## Path rules

A path is valid only when all of the following hold:

- It is a non-empty string.
- It uses `/` separators; `\` is rejected.
- It has no drive letter or colon.
- It is not absolute.
- It contains no empty, `.` or `..` segments.
- It is canonical: no duplicate separators, no trailing separator.
- When resolved under the project root, it stays inside the project root.

`safe_project_path()` resolves a stored path under the project root and raises
`ValueError` if the result is the root itself or escapes it. This check runs before any
file is opened.

## Checksums and integrity

`verify_integrity(root, manifest)` returns a list of human-readable problems. An empty
list means the project is sound. It reports:

- paths that fail validation,
- missing files,
- paths that are not regular files,
- checksum mismatches.

Checksums are computed over the raw file bytes. A dataset is trusted only when its
checksum matches. Integrity checking can skip hashing for a fast structural check, but
opening a project for analysis should verify it.

## Writing a project

`init_project(root, name)`:

1. Refuses to overwrite an existing `project.json`.
2. Creates the directory and the standard subdirectories.
3. Generates a UUID if one is not supplied.
4. Writes the manifest atomically.

`save_manifest(root, manifest)`:

1. Merges or updates fields as requested and refreshes `updated_at`.
2. Serializes JSON with UTF-8, LF newlines and stable field order.
3. Writes to `.project-*.tmp` in the project root.
4. Flushes and `fsync`s the file.
5. Replaces `project.json` atomically.
6. Deletes the temporary file on any failure.

Temporary files are not valid project files and are ignored by readers.

## Versioning and migration

- `manifest_version` is the project format version, currently `"1.0"`.
- `app_version` records the application that wrote the file.
- Readers reject unsupported manifest versions with a clear error.
- A future format version must ship a migration that reads the previous version and writes
  the new one. Migrations operate on JSON documents only and never import project data as
  code.
- Unknown top-level fields are not preserved by the current reader. Add fields together
  with a format-version decision.

## What is not stored yet

The following are reserved by the layout and will be specified when their phases land:

- `analyses/*.json` stored `AnalysisResult` documents,
- `dashboards/*.json` dashboard specifications,
- `models/` model artifacts and registry metadata,
- `lineage.json` derived-table lineage.

They will follow the same rules: versioned JSON, relative paths, no executable payloads,
and explicit integrity metadata where files are referenced.
