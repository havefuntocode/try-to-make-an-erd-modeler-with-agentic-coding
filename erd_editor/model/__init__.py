"""Neutrales Datenmodell (dataclasses, ohne Qt)."""

from erd_editor.model.layout import Layout
from erd_editor.model.schema import (
    Column,
    ConnectionProfile,
    Database,
    Diagram,
    Dialect,
    ForeignKey,
    Index,
    IndexKind,
    Schema,
    Table,
    TableRef,
)

__all__ = [
    "Column",
    "ConnectionProfile",
    "Database",
    "Diagram",
    "Dialect",
    "ForeignKey",
    "Index",
    "IndexKind",
    "Layout",
    "Schema",
    "Table",
    "TableRef",
]
