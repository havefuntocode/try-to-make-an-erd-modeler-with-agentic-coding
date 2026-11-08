"""Analyse des Modells: Beziehungen, Kardinalitäten, identifizierende FKs."""

from erd_editor.analysis.overrides import (
    RelationshipKey,
    RelationshipOverride,
    apply_overrides,
    describe_override,
    relationship_key,
)
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
    "RelationshipKey",
    "RelationshipOverride",
    "apply_overrides",
    "describe_override",
    "relationship_key",
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
