"""Diagrammdatei (`.erd.json`): Schema, Layout und Verbindungsprofile getrennt.

Aufbau:

    {
      "format": "erd-editor", "version": 1,
      "connections": [ {name, dialect, host, port, user, options} ],   # ohne Passwort
      "schema":      { "databases": [ ... neutrales Modell ... ] },
      "layout":      { "notation": ..., "databases": [...], "entities": [...] }
    }

Tabellen werden im Layout über ihre Bestandteile (database, schema, table)
adressiert, nicht über einen zusammengesetzten Schlüssel – Namen dürfen
Punkte enthalten.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from erd_editor.model import (
    Column,
    ConnectionProfile,
    Database,
    Diagram,
    Dialect,
    ForeignKey,
    Index,
    IndexKind,
    Layout,
    Schema,
    Table,
    TableRef,
)

FORMAT = "erd-editor"
VERSION = 1
FILE_SUFFIX = ".erd.json"


class DocumentError(Exception):
    """Datei ist keine gültige Diagrammdatei."""


@dataclass
class DiagramDocument:
    diagram: Diagram = field(default_factory=Diagram)
    layout: Layout = field(default_factory=Layout)
    connections: list[ConnectionProfile] = field(default_factory=list)

    def connection(self, name: str | None) -> ConnectionProfile | None:
        return next((c for c in self.connections if c.name == name), None)


# ---------------------------------------------------------------------- Laden/Speichern


def save_document(document: DiagramDocument, path: str | os.PathLike) -> None:
    """Atomar speichern; eine vorhandene Datei wird vorher zu `.bak`."""
    path = Path(path)
    text = json.dumps(document_to_dict(document), ensure_ascii=False, indent=2)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text + "\n", encoding="utf-8")
    if path.exists():
        os.replace(path, path.with_name(path.name + ".bak"))
    os.replace(tmp, path)


def load_document(path: str | os.PathLike) -> DiagramDocument:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DocumentError(f"Keine gültige JSON-Datei: {exc}") from exc
    return document_from_dict(data)


# ---------------------------------------------------------------------- Dokument ↔ dict


def document_to_dict(doc: DiagramDocument) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "version": VERSION,
        "connections": [_profile_to_dict(p) for p in doc.connections],
        "schema": {"databases": [_database_to_dict(db) for db in doc.diagram.databases]},
        "layout": _layout_to_dict(doc.layout),
    }


def document_from_dict(data: Any) -> DiagramDocument:
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise DocumentError("Datei ist keine Diagrammdatei des ER-Diagramm-Editors")
    version = data.get("version")
    if not isinstance(version, int) or version > VERSION:
        raise DocumentError(f"Dateiversion {version!r} wird nicht unterstützt (höchstens {VERSION})")
    try:
        return DiagramDocument(
            diagram=Diagram([_database_from_dict(d) for d in data.get("schema", {}).get("databases", [])]),
            layout=_layout_from_dict(data.get("layout", {})),
            connections=[_profile_from_dict(p) for p in data.get("connections", [])],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DocumentError(f"Diagrammdatei ist beschädigt: {exc!r}") from exc


# ---------------------------------------------------------------------- Verbindungen


def _profile_to_dict(p: ConnectionProfile) -> dict[str, Any]:
    # Bewusst ohne Passwort – das liegt im keyring
    return {"name": p.name, "dialect": p.dialect.value, "host": p.host, "port": p.port, "user": p.user, "options": dict(p.options)}


def _profile_from_dict(d: dict[str, Any]) -> ConnectionProfile:
    return ConnectionProfile(d["name"], Dialect(d["dialect"]), d["host"], d.get("port"), d.get("user"), dict(d.get("options", {})))


# ---------------------------------------------------------------------- Schema


def _database_to_dict(db: Database) -> dict[str, Any]:
    return {
        "name": db.name,
        "dialect": db.dialect.value,
        "connection": db.connection_profile,
        "server_version": db.server_version,
        "charset": db.charset,
        "collation": db.collation,
        "schemas": [{"name": s.name, "tables": [_table_to_dict(t) for t in s.tables]} for s in db.schemas],
    }


def _database_from_dict(d: dict[str, Any]) -> Database:
    return Database(
        name=d["name"],
        dialect=Dialect(d["dialect"]),
        schemas=[Schema(s.get("name"), [_table_from_dict(t) for t in s.get("tables", [])]) for s in d.get("schemas", [])],
        connection_profile=d.get("connection"),
        server_version=d.get("server_version"),
        charset=d.get("charset"),
        collation=d.get("collation"),
    )


def _table_to_dict(t: Table) -> dict[str, Any]:
    return {
        "name": t.name,
        "comment": t.comment,
        "columns": [
            {
                "name": c.name,
                "type": c.data_type,
                "nullable": c.nullable,
                "default": c.default,
                "comment": c.comment,
                "auto_increment": c.auto_increment,
            }
            for c in t.columns
        ],
        "indexes": [{"name": i.name, "kind": i.kind.value, "columns": list(i.columns)} for i in t.indexes],
        "foreign_keys": [
            {
                "name": fk.name,
                "columns": list(fk.columns),
                "references": _ref_to_dict(fk.referenced_table),
                "referenced_columns": list(fk.referenced_columns),
                "on_update": fk.on_update,
                "on_delete": fk.on_delete,
            }
            for fk in t.foreign_keys
        ],
    }


def _table_from_dict(d: dict[str, Any]) -> Table:
    return Table(
        name=d["name"],
        comment=d.get("comment"),
        columns=[
            Column(
                c["name"],
                c["type"],
                nullable=c.get("nullable", True),
                default=c.get("default"),
                comment=c.get("comment"),
                auto_increment=c.get("auto_increment", False),
            )
            for c in d.get("columns", [])
        ],
        indexes=[Index(i["name"], IndexKind(i["kind"]), tuple(i["columns"])) for i in d.get("indexes", [])],
        foreign_keys=[
            ForeignKey(
                fk["name"],
                tuple(fk["columns"]),
                _ref_from_dict(fk["references"]),
                tuple(fk["referenced_columns"]),
                fk.get("on_update"),
                fk.get("on_delete"),
            )
            for fk in d.get("foreign_keys", [])
        ],
    )


def _ref_to_dict(ref: TableRef) -> dict[str, Any]:
    return {"database": ref.database, "schema": ref.schema, "table": ref.table}


def _ref_from_dict(d: dict[str, Any]) -> TableRef:
    return TableRef(d["database"], d.get("schema"), d["table"])


# ---------------------------------------------------------------------- Layout


def _layout_to_dict(layout: Layout) -> dict[str, Any]:
    return {
        "notation": layout.notation,
        "databases": [{"name": name, "color": color} for name, color in sorted(layout.database_colors.items())],
        "entities": [
            {**_ref_to_dict(ref), "x": round(x, 1), "y": round(y, 1)}
            for ref, (x, y) in sorted(layout.positions.items(), key=lambda kv: _sort_key(kv[0]))
        ],
    }


def _sort_key(ref: TableRef) -> tuple[str, str, str]:
    """Stabile Reihenfolge in der Datei (schema darf None sein)."""
    return (ref.database, ref.schema or "", ref.table)


def _layout_from_dict(d: dict[str, Any]) -> Layout:
    return Layout(
        positions={_ref_from_dict(e): (float(e["x"]), float(e["y"])) for e in d.get("entities", [])},
        database_colors={db["name"]: int(db["color"]) for db in d.get("databases", [])},
        notation=d.get("notation", "crowsfoot"),
    )
