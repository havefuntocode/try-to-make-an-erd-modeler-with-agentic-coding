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
from dataclasses import dataclass, replace

from erd_editor.model.schema import Column, Database, Diagram, Dialect, ForeignKey, Index, IndexKind, Schema, Table, TableRef
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
    final_indexes: bool = False,
) -> Diagram:
    """Tabelle ersetzen (bearbeitet); Umbenennungen werden überall nachgezogen.

    `table.name` ist der (ggf. neue) Tabellenname, `column_renames` bildet
    alte auf neue Spaltennamen ab. Spalten, die es in `table` nicht mehr
    gibt, gelten als gelöscht: Indexe und Fremdschlüssel mit diesen
    Spalten entfallen – auch Fremdschlüssel anderer Tabellen, die auf sie zeigen.

    `final_indexes=True`: `table.indexes` nennen bereits die neuen
    Spaltennamen (Tabelleneditor) und werden nicht umbenannt, nur geprüft.
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
    if final_indexes:
        for index in edited.indexes:
            unknown = [c for c in index.columns if c not in present]
            if unknown or not index.columns:
                raise EditError(f"Index {index.name!r} nennt unbekannte Spalten: {', '.join(unknown) or '(keine)'}")
        index_names = [i.name for i in edited.indexes]
        if len(index_names) != len(set(index_names)):
            raise EditError("Indexnamen müssen eindeutig sein")
    else:
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


# ---------------------------------------------------------------------- Beziehungen


@dataclass(frozen=True)
class RelationshipPlan:
    """Was `add_relationship` anlegen würde (auch für die Vorschau im Dialog)."""

    fk_name: str
    columns: tuple[Column, ...]  # neue FK-Spalten in der Kindtabelle
    referenced_columns: tuple[str, ...]  # PK-Spalten der Elterntabelle
    unique_index: str | None  # Name des UNIQUE-Index bei 1:1
    adds_to_primary_key: bool


def _unique(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    n = 2
    while f"{name}_{n}" in taken:
        n += 1
    return f"{name}_{n}"


def plan_relationship(
    diagram: Diagram,
    child: TableRef,
    parent: TableRef,
    one_to_one: bool = False,
    identifying: bool = False,
    mandatory: bool = True,
) -> RelationshipPlan:
    """Fremdschlüssel von `child` auf `parent` planen, ohne etwas zu ändern.

    FK-Spalten heißen wie die PK-Spalten der Elterntabelle; `id` wird zu
    `<eltern>_id`. Bei Namenskonflikten wird hochgezählt (`kunde_id_2`).
    """
    if not is_editable(diagram, child.database):
        raise EditError(f"Datenbank {child.database!r} ist eingelesen und schreibgeschützt")
    if child.database != parent.database:
        raise EditError("Beziehungen sind nur innerhalb einer Datenbank möglich")
    child_table, parent_table = diagram.find_table(child), diagram.find_table(parent)
    if child_table is None or parent_table is None:
        raise EditError("Tabelle nicht gefunden")
    pk = parent_table.primary_key_columns
    if not pk:
        raise EditError(f"Tabelle {parent.table!r} hat keinen Primärschlüssel – darauf kann kein Fremdschlüssel zeigen")
    if identifying and child == parent:
        raise EditError("Eine Selbstreferenz kann nicht identifizierend sein")

    taken = {c.name for c in child_table.columns}
    columns = []
    for name in pk:
        source = parent_table.column(name)
        base = f"{parent.table}_{name}" if name == "id" or child == parent else name
        new_name = _unique(base, taken)
        taken.add(new_name)
        columns.append(Column(new_name, source.data_type, nullable=not (mandatory or identifying)))

    fk_names = {fk.name for ref, t in diagram.tables() if ref.database == child.database for fk in t.foreign_keys}
    fk_name = _unique(f"fk_{child.table}_{parent.table}", fk_names)
    index_names = {i.name for i in child_table.indexes}
    unique_index = _unique(f"uq_{child.table}_{parent.table}", index_names) if one_to_one else None
    return RelationshipPlan(fk_name, tuple(columns), pk, unique_index, identifying)


def add_relationship(
    diagram: Diagram,
    child: TableRef,
    parent: TableRef,
    one_to_one: bool = False,
    identifying: bool = False,
    mandatory: bool = True,
) -> tuple[Diagram, RelationshipPlan]:
    """Fremdschlüssel samt Spalten anlegen; liefert neues Diagramm und den Plan."""
    plan = plan_relationship(diagram, child, parent, one_to_one, identifying, mandatory)
    new = _clone(diagram)
    table = new.find_table(child)
    table.columns.extend(copy.deepcopy(list(plan.columns)))
    new_cols = tuple(c.name for c in plan.columns)
    if identifying:
        pk = table.primary_key
        if pk is None:
            pk_name = "PRIMARY" if new.database(child.database).dialect is Dialect.MYSQL else f"{child.table}_pkey"
            table.indexes.insert(0, Index(pk_name, IndexKind.PRIMARY, new_cols))
        else:
            table.indexes[table.indexes.index(pk)] = replace(pk, columns=pk.columns + new_cols)
    if plan.unique_index:
        table.indexes.append(Index(plan.unique_index, IndexKind.UNIQUE, new_cols))
    table.foreign_keys.append(ForeignKey(plan.fk_name, new_cols, parent, plan.referenced_columns))
    return new, plan


def delete_relationship(diagram: Diagram, child: TableRef, fk_name: str, drop_columns: bool = True) -> Diagram:
    """Fremdschlüssel entfernen; auf Wunsch auch seine Spalten (samt Indexen mit diesen Spalten)."""
    if not is_editable(diagram, child.database):
        raise EditError(f"Datenbank {child.database!r} ist eingelesen und schreibgeschützt")
    new = _clone(diagram)
    table = new.find_table(child)
    fk = next((f for f in table.foreign_keys if f.name == fk_name), None) if table else None
    if fk is None:
        raise EditError(f"Fremdschlüssel {fk_name!r} gibt es nicht")
    table.foreign_keys.remove(fk)
    if drop_columns:
        # nur Spalten entfernen, die kein anderer Fremdschlüssel braucht
        still_used = {c for other in table.foreign_keys for c in other.columns}
        drop = set(fk.columns) - still_used
        table.columns = [c for c in table.columns if c.name not in drop]
        kept = []
        for index in table.indexes:
            remaining = tuple(c for c in index.columns if c not in drop)
            # PK schrumpft (bleibt, solange Spalten übrig sind); andere Indexe mit gelöschten Spalten entfallen
            if remaining and (index.kind is IndexKind.PRIMARY or remaining == index.columns):
                kept.append(replace(index, columns=remaining))
        table.indexes = kept
    return new
