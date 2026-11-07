"""Analyse des Modells: Beziehungen, Kardinalitäten, identifizierende FKs."""

from erd_editor.analysis.relationships import (
    EXACTLY_ONE,
    ZERO_OR_MANY,
    ZERO_OR_ONE,
    Cardinality,
    Relationship,
    child_cardinality,
    derive_relationships,
    is_identifying,
    is_junction_table,
    parent_cardinality,
)

__all__ = [
    "EXACTLY_ONE",
    "ZERO_OR_MANY",
    "ZERO_OR_ONE",
    "Cardinality",
    "Relationship",
    "child_cardinality",
    "derive_relationships",
    "is_identifying",
    "is_junction_table",
    "parent_cardinality",
]
