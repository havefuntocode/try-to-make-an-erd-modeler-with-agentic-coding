"""Teildiagramme: benannte Ausschnitte eines Diagramms mit eigenem Layout.

Das Schema gibt es nur einmal; ein Teildiagramm wählt Tabellen daraus
aus und hat eigene Positionen, Rahmen und Darstellungsoptionen.
Keine Qt-Abhängigkeit.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from erd_editor.model.layout import Layout
from erd_editor.model.schema import Diagram, Schema, TableRef


@dataclass
class Subdiagram:
    name: str
    tables: list[TableRef] = field(default_factory=list)
    layout: Layout | None = None  # None: beim ersten Anzeigen automatisch anordnen


def filter_diagram(diagram: Diagram, tables: list[TableRef] | set[TableRef]) -> Diagram:
    """Diagramm nur mit den gewünschten Tabellen.

    Tabellen-Objekte werden geteilt (keine Kopie); Datenbanken und Schemas
    ohne gewählte Tabellen entfallen. Reihenfolge wie im Diagramm.
    """
    wanted = set(tables)
    databases = []
    for db in diagram.databases:
        schemas = []
        for schema in db.schemas:
            kept = [t for t in schema.tables if TableRef(db.name, schema.name, t.name) in wanted]
            if kept:
                schemas.append(Schema(schema.name, kept))
        if schemas:
            databases.append(replace(db, schemas=schemas))
    return Diagram(databases)


def existing_tables(diagram: Diagram, tables: list[TableRef]) -> list[TableRef]:
    """Nur die Tabellen, die es im Diagramm (noch) gibt – Reihenfolge bleibt."""
    present = {ref for ref, _ in diagram.tables()}
    return [ref for ref in tables if ref in present]


def neighbours(diagram: Diagram, tables: set[TableRef]) -> set[TableRef]:
    """Tabellen, die über einen Fremdschlüssel direkt mit `tables` verbunden sind."""
    result: set[TableRef] = set()
    for ref, table in diagram.tables():
        for fk in table.foreign_keys:
            target = fk.referenced_table
            if ref in tables and diagram.find_table(target) is not None:
                result.add(target)
            if target in tables:
                result.add(ref)
    return result - set(tables)


def unique_name(name: str, taken: set[str]) -> str:
    """`Bestellwesen`, bei Bedarf `Bestellwesen 2`, `Bestellwesen 3` …"""
    if name not in taken:
        return name
    n = 2
    while f"{name} {n}" in taken:
        n += 1
    return f"{name} {n}"


__all__ = ["Subdiagram", "existing_tables", "filter_diagram", "neighbours", "unique_name"]
