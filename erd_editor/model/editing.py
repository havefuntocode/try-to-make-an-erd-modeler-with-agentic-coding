"""Bearbeitungsoperationen auf dem neutralen Modell (für Version 3 – Modellieren).

Alle Funktionen arbeiten auf einer **Kopie** und geben ein neues Diagramm
zurück; das alte bleibt unverändert – so kann Rückgängig einfach den
alten Stand wiederherstellen.

Umbenennen ist referenzsicher: Fremdschlüssel und Indexe, die auf eine
umbenannte Tabelle oder Spalte zeigen, werden nachgezogen; Verweise auf
entfernte Spalten oder Tabellen entfallen.
"""

from __future__ import annotations

import copy
from dataclasses import replace

from erd_editor.model.schema import Database, Diagram, Dialect, ForeignKey, Index, Schema, Table, TableRef
from erd_editor.model.types import DEFAULT_SCHEMAS


class EditError(ValueError):
    """Bearbeitung nicht möglich (z. B. Name schon vergeben)."""


def _clone(diagram: Diagram) -> Diagram:
    return copy.deepcopy(diagram)


def _schema(diagram: Diagram, database: str, schema: str | None) -> Schema:
    db = diagram.database(database)
    if db is None:
        raise EditError(f"Datenbank {database!r} gibt es nicht")
    sch = db.schema(schema)
    if sch is None:
        raise EditError(f"Schema {schema!r} gibt es in {database!r} nicht")
    return sch


def is_editable(diagram: Diagram, database: str) -> bool:
    db = diagram.database(database)
    return db is not None and db.modeled


# ---------------------------------------------------------------------- Datenbanken


def add_database(diagram: Diagram, name: str, dialect: Dialect, schema: str | None = None) -> Diagram:
    """Neue, leere Modell-Datenbank (ohne Verbindung) anlegen."""
    name = name.strip()
    if not name:
        raise EditError("Bitte einen Namen für die Datenbank angeben")
    if diagram.database(name) is not None:
        raise EditError(f"Eine Datenbank {name!r} gibt es schon")
    schema_name = None if dialect is Dialect.MYSQL else (schema or DEFAULT_SCHEMAS[dialect])
    new = _clone(diagram)
    new.databases.append(Database(name, dialect, [Schema(schema_name, [])], modeled=True))
    return new


def take_over_as_model(diagram: Diagram, database: str) -> Diagram:
    """Eingelesene Datenbank bearbeitbar machen (Verbindung wird gelöst)."""
    new = _clone(diagram)
    db = new.database(database)
    if db is None:
        raise EditError(f"Datenbank {database!r} gibt es nicht")
    db.modeled = True
    db.connection_profile = None
    return new


# ---------------------------------------------------------------------- Tabellen


def add_table(diagram: Diagram, ref: TableRef, table: Table) -> Diagram:
    """Neue Tabelle in eine Modell-Datenbank einfügen."""
    if not is_editable(diagram, ref.database):
        raise EditError(f"Datenbank {ref.database!r} ist eingelesen und schreibgeschützt")
    if diagram.find_table(ref) is not None:
        raise EditError(f"Eine Tabelle {ref.table!r} gibt es dort schon")
    new = _clone(diagram)
    _schema(new, ref.database, ref.schema).tables.append(replace(copy.deepcopy(table), name=ref.table))
    return new


def replace_table(
    diagram: Diagram,
    ref: TableRef,
    table: Table,
    column_renames: dict[str, str] | None = None,
) -> Diagram:
    """Tabelle ersetzen (bearbeitet); Umbenennungen werden überall nachgezogen.

    `table.name` ist der (ggf. neue) Tabellenname, `column_renames` bildet
    alte auf neue Spaltennamen ab. Spalten, die es in `table` nicht mehr
    gibt, gelten als gelöscht: Indexe und Fremdschlüssel mit diesen
    Spalten entfallen – auch Fremdschlüssel anderer Tabellen, die auf sie zeigen.
    """
    if not is_editable(diagram, ref.database):
        raise EditError(f"Datenbank {ref.database!r} ist eingelesen und schreibgeschützt")
    old = diagram.find_table(ref)
    if old is None:
        raise EditError(f"Tabelle {ref} gibt es nicht")
    renames = dict(column_renames or {})
    new_ref = TableRef(ref.database, ref.schema, table.name)
    if new_ref != ref and diagram.find_table(new_ref) is not None:
        raise EditError(f"Eine Tabelle {table.name!r} gibt es dort schon")
    names = [c.name for c in table.columns]
    if len(names) != len(set(names)):
        raise EditError("Spaltennamen müssen eindeutig sein")
    present = set(names)

    def map_own(col: str) -> str | None:
        name = renames.get(col, col)
        return name if name in present else None

    new = _clone(diagram)
    schema = _schema(new, ref.database, ref.schema)
    edited = copy.deepcopy(table)
    # eigene Indexe und FKs: Umbenennungen anwenden, gelöschte Spalten entfernen
    edited.indexes = _remap_indexes(edited.indexes, map_own)
    edited.foreign_keys = _remap_fks(edited.foreign_keys, map_own)
    # Selbstreferenzen zeigen ggf. auf den neuen Namen
    for fk in edited.foreign_keys:
        if fk.referenced_table == ref:
            fk.referenced_table = new_ref
            fk.referenced_columns = tuple(map_own(c) or c for c in fk.referenced_columns)
    schema.tables[[t.name for t in schema.tables].index(ref.table)] = edited

    # Fremdschlüssel anderer Tabellen nachziehen (alle Datenbanken: MySQL erlaubt DB-übergreifende FKs)
    for other_ref, other in new.tables():
        if other_ref == new_ref:
            continue
        kept = []
        for fk in other.foreign_keys:
            if fk.referenced_table == ref:
                mapped = tuple(map_own(c) for c in fk.referenced_columns)
                if any(c is None for c in mapped):
                    continue  # referenzierte Spalte gelöscht → FK entfällt
                fk.referenced_table = new_ref
                fk.referenced_columns = mapped
            kept.append(fk)
        other.foreign_keys = kept
    return new


def delete_table(diagram: Diagram, ref: TableRef) -> Diagram:
    """Tabelle löschen; Fremdschlüssel anderer Tabellen auf sie entfallen."""
    if not is_editable(diagram, ref.database):
        raise EditError(f"Datenbank {ref.database!r} ist eingelesen und schreibgeschützt")
    new = _clone(diagram)
    schema = _schema(new, ref.database, ref.schema)
    schema.tables = [t for t in schema.tables if t.name != ref.table]
    for _, other in new.tables():
        other.foreign_keys = [fk for fk in other.foreign_keys if fk.referenced_table != ref]
    return new


# ---------------------------------------------------------------------- Hilfen


def _remap_indexes(indexes: list[Index], mapping) -> list[Index]:
    result = []
    for index in indexes:
        cols = tuple(mapping(c) for c in index.columns)
        if all(c is not None for c in cols):
            result.append(replace(index, columns=cols))
    return result


def _remap_fks(fks: list[ForeignKey], mapping) -> list[ForeignKey]:
    result = []
    for fk in fks:
        cols = tuple(mapping(c) for c in fk.columns)
        if all(c is not None for c in cols):
            fk.columns = cols
            result.append(fk)
    return result
