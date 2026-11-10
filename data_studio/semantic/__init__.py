"""Semantic model and measure language.

The semantic layer sits between stored datasets and dashboards:

* :mod:`data_studio.semantic.dsl` parses safe measure expressions and compiles them to SQL.
* :mod:`data_studio.semantic.model` defines tables, relationships, dimensions and measures.
* :mod:`data_studio.semantic.store` persists the model under ``models/semantic.json``.
* :mod:`data_studio.semantic.engine` executes typed queries with relationship joins.
"""

from .dsl import (
    CompiledMeasure,
    MeasureError,
    ResolvedColumn,
    compile_measure,
    referenced_columns,
)
from .engine import (
    QueryFilter,
    QueryOrder,
    SemanticColumn,
    SemanticEngine,
    SemanticQuery,
    SemanticResult,
)
from .model import (
    GRAINS,
    GRAIN_PERIODS,
    MEASURE_FORMATS,
    MODEL_SCHEMA_VERSION,
    RELATIONSHIP_KINDS,
    Relationship,
    SemanticDimension,
    SemanticError,
    SemanticHierarchy,
    SemanticMeasure,
    SemanticModel,
    SemanticTable,
)
from .calendar import CALENDAR_DATASET, CalendarError, calendar_sql, generate_calendar
from .store import MODEL_PATH, SemanticModelStore

__all__ = [
    "CALENDAR_DATASET",
    "GRAINS",
    "GRAIN_PERIODS",
    "MEASURE_FORMATS",
    "MODEL_PATH",
    "MODEL_SCHEMA_VERSION",
    "RELATIONSHIP_KINDS",
    "CalendarError",
    "CompiledMeasure",
    "MeasureError",
    "QueryFilter",
    "QueryOrder",
    "Relationship",
    "ResolvedColumn",
    "SemanticColumn",
    "SemanticDimension",
    "SemanticEngine",
    "SemanticError",
    "SemanticHierarchy",
    "SemanticMeasure",
    "SemanticModel",
    "SemanticModelStore",
    "SemanticQuery",
    "SemanticResult",
    "SemanticTable",
    "calendar_sql",
    "compile_measure",
    "generate_calendar",
    "referenced_columns",
]
