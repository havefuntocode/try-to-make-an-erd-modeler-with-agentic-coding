"""Ableitung von Beziehungen aus den Fremdschlüsseln des neutralen Modells.

Regeln (siehe PROJEKT.md, Abschnitt 6):

- **Identifizierend**: alle FK-Spalten gehören zum Primärschlüssel der Kindtabelle.
- **Elternseite** (wie viele Eltern hat ein Kind?):
  eine FK-Spalte NULL-fähig → 0..1, sonst 1..1.
- **Kindseite** (wie viele Kinder hat ein Elternteil?):
  ein PK/UNIQUE-Index, dessen Spalten vollständig in den FK-Spalten liegen,
  macht den FK eindeutig → 0..1 (also 1:1), sonst 0..n (1:n).
  Die Mindestkardinalität „mindestens ein Kind“ ist nicht ableitbar und bleibt 0.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from erd_editor.model import Diagram, ForeignKey, Table, TableRef


@dataclass(frozen=True)
class Cardinality:
    """Ende einer Beziehung; `maximum=None` bedeutet „viele“."""

    minimum: int
    maximum: int | None

    @property
    def is_optional(self) -> bool:
        return self.minimum == 0

    @property
    def is_many(self) -> bool:
        return self.maximum is None

    def __str__(self) -> str:
        return f"{self.minimum}..{'*' if self.maximum is None else self.maximum}"


ZERO_OR_ONE = Cardinality(0, 1)
EXACTLY_ONE = Cardinality(1, 1)
ZERO_OR_MANY = Cardinality(0, None)


@dataclass(frozen=True)
class Relationship:
    """Aus einem Fremdschlüssel abgeleitete Beziehung Kind → Elternteil.

    `parallel_index`/`parallel_count` nummerieren Beziehungen zwischen
    demselben Tabellenpaar (in beliebiger Richtung), damit die Darstellung
    sie versetzt zeichnen kann.
    """

    child: TableRef
    parent: TableRef
    foreign_key: ForeignKey
    identifying: bool
    parent_end: Cardinality
    child_end: Cardinality
    parallel_index: int = 0
    parallel_count: int = 1

    @property
    def name(self) -> str:
        return self.foreign_key.name

    @property
    def is_self_reference(self) -> bool:
        return self.child == self.parent

    @property
    def is_cross_database(self) -> bool:
        return self.child.database != self.parent.database

    @property
    def is_one_to_one(self) -> bool:
        return not self.child_end.is_many


def is_identifying(table: Table, fk: ForeignKey) -> bool:
    """True, wenn alle FK-Spalten im Primärschlüssel der Kindtabelle liegen."""
    pk = set(table.primary_key_columns)
    return bool(pk) and set(fk.columns) <= pk


def parent_cardinality(table: Table, fk: ForeignKey) -> Cardinality:
    """Elternseite: optional, sobald eine FK-Spalte NULL-fähig ist.

    Bei mehrspaltigen FKs genügt eine NULL-fähige Spalte, denn mit einem
    NULL-Wert wird der FK (MATCH SIMPLE) nicht geprüft.
    Unbekannte Spalten gelten als NULL-fähig.
    """
    for name in fk.columns:
        column = table.column(name)
        if column is None or column.nullable:
            return ZERO_OR_ONE
    return EXACTLY_ONE


def child_cardinality(table: Table, fk: ForeignKey) -> Cardinality:
    """Kindseite: höchstens ein Kind, wenn ein eindeutiger Index die FK-Spalten eindeutig macht."""
    fk_columns = set(fk.columns)
    for index in table.indexes:
        if index.is_unique and index.columns and set(index.columns) <= fk_columns:
            return ZERO_OR_ONE
    return ZERO_OR_MANY


def derive_relationships(diagram: Diagram) -> list[Relationship]:
    """Alle Beziehungen des Diagramms ableiten.

    Fremdschlüssel, deren Zieltabelle nicht im Diagramm liegt, werden
    übersprungen; sie meldet `Diagram.validate()`.
    Die Reihenfolge folgt der Reihenfolge der Tabellen und FKs im Modell.
    """
    raw: list[Relationship] = []
    for ref, table in diagram.tables():
        for fk in table.foreign_keys:
            if diagram.find_table(fk.referenced_table) is None:
                continue
            raw.append(
                Relationship(
                    child=ref,
                    parent=fk.referenced_table,
                    foreign_key=fk,
                    identifying=is_identifying(table, fk),
                    parent_end=parent_cardinality(table, fk),
                    child_end=child_cardinality(table, fk),
                )
            )

    # Parallele Beziehungen zwischen demselben (ungeordneten) Tabellenpaar nummerieren
    groups: dict[frozenset[TableRef], list[int]] = defaultdict(list)
    for i, rel in enumerate(raw):
        groups[frozenset((rel.child, rel.parent))].append(i)

    result = list(raw)
    for indices in groups.values():
        for n, i in enumerate(indices):
            rel = raw[i]
            result[i] = Relationship(
                child=rel.child,
                parent=rel.parent,
                foreign_key=rel.foreign_key,
                identifying=rel.identifying,
                parent_end=rel.parent_end,
                child_end=rel.child_end,
                parallel_index=n,
                parallel_count=len(indices),
            )
    return result


def is_junction_table(table: Table) -> bool:
    """Erkennt n:m-Zwischentabellen.

    Kriterium: mindestens zwei identifizierende FKs, deren Spalten
    zusammen den kompletten Primärschlüssel ergeben.
    """
    pk = set(table.primary_key_columns)
    identifying = [fk for fk in table.foreign_keys if is_identifying(table, fk)]
    if not pk or len(identifying) < 2:
        return False
    covered = set().union(*(set(fk.columns) for fk in identifying))
    return covered == pk
