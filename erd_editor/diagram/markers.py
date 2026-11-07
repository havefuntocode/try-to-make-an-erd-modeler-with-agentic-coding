"""Auswahl der Spaltensymbole – reine Logik ohne Qt, damit testbar."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from erd_editor.model import Column, Index, IndexKind, Table


class KeyIcon(str, Enum):
    """Hauptsymbol links neben dem Spaltennamen; Wert = Dateiname ohne .svg."""

    PRIMARY = "key_pk"
    FOREIGN = "key_fk"
    PRIMARY_FOREIGN = "key_pkfk"
    NOT_NULL = "not_null"
    NULLABLE = "nullable"


INDEXED_ICON = "indexed"


@dataclass(frozen=True)
class ColumnMarker:
    icon: KeyIcon
    indexed: bool  # Zusatzsymbol „indiziert“ (bei PK-Spalten nicht nötig)


def column_marker(table: Table, column: Column) -> ColumnMarker:
    """Symbol für eine Spalte bestimmen."""
    is_pk = table.is_primary_key_column(column.name)
    is_fk = table.is_foreign_key_column(column.name)
    if is_pk and is_fk:
        icon = KeyIcon.PRIMARY_FOREIGN
    elif is_pk:
        icon = KeyIcon.PRIMARY
    elif is_fk:
        icon = KeyIcon.FOREIGN
    elif column.nullable:
        icon = KeyIcon.NULLABLE
    else:
        icon = KeyIcon.NOT_NULL
    # PK-Spalten sind immer indiziert – dort wäre das Zusatzsymbol nur Rauschen
    indexed = not is_pk and table.is_indexed_column(column.name)
    return ColumnMarker(icon, indexed)


def index_label(index: Index) -> tuple[str, str]:
    """Beschriftung einer Zeile im Indexbereich: (Art, Name und Spalten).

    Beim Primärschlüssel entfällt der Name, z. B. `("PRIMARY", "(id)")`,
    sonst `("UNIQUE", "uq_kunde (kunde_id)")`.
    """
    columns = f"({', '.join(index.columns)})"
    if index.kind is IndexKind.PRIMARY:
        return index.kind.value, columns
    return index.kind.value, f"{index.name} {columns}"
