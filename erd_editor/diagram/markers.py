"""Auswahl der Spaltensymbole – reine Logik ohne Qt, damit testbar."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from erd_editor.model import CheckConstraint, Column, Index, IndexKind, Table


class KeyIcon(str, Enum):
    """Hauptsymbol links neben dem Spaltennamen; Wert = Dateiname ohne .svg."""

    PRIMARY = "key_pk"
    FOREIGN = "key_fk"
    PRIMARY_FOREIGN = "key_pkfk"
    NOT_NULL = "not_null"
    NULLABLE = "nullable"


class ExtraIcon(str, Enum):
    """Kleines Zusatzsymbol hinter dem Hauptsymbol; Wert = Dateiname ohne .svg.

    Gefüllt/leer wie bei den Rauten (NOT NULL/NULL): gefülltes UNIQUE = Spalte
    allein eindeutig, leeres UNIQUE = Teil einer eindeutigen Kombination.
    """

    INDEXED = "indexed"
    UNIQUE = "unique"
    UNIQUE_PART = "unique_part"


INDEXED_ICON = ExtraIcon.INDEXED.value


class CheckIcon(str, Enum):
    """Symbol am rechten Zeilenende für CHECK-Constraints; Wert = Dateiname ohne .svg.

    Gefüllt = ein Check nur für diese Spalte, leer = Teil eines Checks über mehrere Spalten.
    """

    CHECK = "check"
    CHECK_PART = "check_part"


def column_check_icon(table: Table, column: Column) -> CheckIcon | None:
    """Check-Symbol einer Spalte: eigener Check hat Vorrang vor „Teil eines Checks“."""
    if table.column_checks(column.name):
        return CheckIcon.CHECK
    if any(column.name in c.columns for c in table.checks):
        return CheckIcon.CHECK_PART
    return None


def check_label(check: CheckConstraint) -> tuple[str, str]:
    """Beschriftung einer Check-Zeile im unteren Bereich: `("CHECK", "ck_preis: preis > 0")`."""
    return "CHECK", f"{check.name}: {check.expression}"


@dataclass(frozen=True)
class ColumnMarker:
    icon: KeyIcon
    extra: ExtraIcon | None = None  # Zusatzsymbol (bei PK-Spalten meist keins)

    @property
    def indexed(self) -> bool:
        return self.extra is not None


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
    return ColumnMarker(icon, _extra(table, column.name, is_pk))


def _extra(table: Table, name: str, is_pk: bool) -> ExtraIcon | None:
    """Stärkste Aussage gewinnt: allein eindeutig > Teil einer Kombination > indiziert."""
    uniques = [i for i in table.indexes if i.kind is IndexKind.UNIQUE and name in i.columns]
    if any(len(i.columns) == 1 for i in uniques):
        return ExtraIcon.UNIQUE
    if is_pk:
        # PK-Spalten sind immer indiziert – dort wäre das Zusatzsymbol nur Rauschen
        return None
    if uniques:
        return ExtraIcon.UNIQUE_PART
    return ExtraIcon.INDEXED if table.is_indexed_column(name) else None


def column_index_notes(table: Table, column: Column) -> list[str]:
    """Indexe einer Spalte für den Tooltip, z. B. „Teil von UNIQUE uq_t (a, b)“ (ohne PK)."""
    notes = []
    for index in table.indexes:
        if index.kind is IndexKind.PRIMARY or column.name not in index.columns:
            continue
        kind, label = index_label(index)
        notes.append(f"{kind} {label}" if len(index.columns) == 1 else f"Teil von {kind} {label}")
    return notes


def index_label(index: Index) -> tuple[str, str]:
    """Beschriftung einer Zeile im Indexbereich: (Art, Name und Spalten).

    Beim Primärschlüssel entfällt der Name, z. B. `("PRIMARY", "(id)")`,
    sonst `("UNIQUE", "uq_kunde (kunde_id)")`.
    """
    columns = f"({', '.join(index.columns)})"
    if index.kind is IndexKind.PRIMARY:
        return index.kind.value, columns
    return index.kind.value, f"{index.name} {columns}"
