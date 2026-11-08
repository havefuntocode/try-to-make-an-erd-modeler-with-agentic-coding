"""Neutrales Datenmodell (dataclasses, ohne Qt)."""

from erd_editor.model.layout import ColumnMode, DisplayOptions, Layout
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
    "ColumnMode",
    "ConnectionProfile",
    "Database",
    "Diagram",
    "Dialect",
    "DisplayOptions",
    "ForeignKey",
    "Index",
    "IndexKind",
    "Layout",
    "Schema",
    "Table",
    "TableRef",
]
