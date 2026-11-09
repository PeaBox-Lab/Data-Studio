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
    MEASURE_FORMATS,
    MODEL_SCHEMA_VERSION,
    RELATIONSHIP_KINDS,
    Relationship,
    SemanticDimension,
    SemanticError,
    SemanticMeasure,
    SemanticModel,
    SemanticTable,
)
from .store import MODEL_PATH, SemanticModelStore

__all__ = [
    "MEASURE_FORMATS",
    "MODEL_PATH",
    "MODEL_SCHEMA_VERSION",
    "RELATIONSHIP_KINDS",
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
    "SemanticMeasure",
    "SemanticModel",
    "SemanticModelStore",
    "SemanticQuery",
    "SemanticResult",
    "SemanticTable",
    "compile_measure",
    "referenced_columns",
]
