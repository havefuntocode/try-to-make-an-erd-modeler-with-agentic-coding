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

from erd_editor.analysis.overrides import RelationshipKey, RelationshipOverride
from erd_editor.model.layout import MAX_SCALE, MIN_SCALE, ColumnMode, DisplayOptions, Frame, Note
from erd_editor.model import (
    CheckConstraint,
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
    Subdiagram,
    Table,
    TableKind,
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
    subdiagrams: list[Subdiagram] = field(default_factory=list)
    overrides: dict[RelationshipKey, RelationshipOverride] = field(default_factory=dict)

    def subdiagram(self, name: str | None) -> Subdiagram | None:
        return next((s for s in self.subdiagrams if s.name == name), None)

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
        "subdiagrams": [
            {
                "name": sd.name,
                "tables": [_ref_to_dict(r) for r in sorted(sd.tables, key=_sort_key)],
                "layout": _layout_to_dict(sd.layout) if sd.layout is not None else None,
            }
            for sd in doc.subdiagrams
        ],
        "overrides": [
            {
                **_ref_to_dict(child),
                "foreign_key": fk_name,
                "child_min_one": o.child_min_one,
                "identifying": o.identifying,
            }
            for (child, fk_name), o in sorted(doc.overrides.items(), key=lambda kv: (_sort_key(kv[0][0]), kv[0][1]))
            if not o.is_empty
        ],
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
            subdiagrams=[
                Subdiagram(
                    sd["name"],
                    [_ref_from_dict(r) for r in sd.get("tables", [])],
                    _layout_from_dict(sd["layout"]) if sd.get("layout") is not None else None,
                )
                for sd in data.get("subdiagrams", [])
            ],
            overrides={
                (_ref_from_dict(o), o["foreign_key"]): RelationshipOverride(o.get("child_min_one"), o.get("identifying"))
                for o in data.get("overrides", [])
            },
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
        "modeled": db.modeled,
        "schemas": [{"name": s.name, "tables": [_table_to_dict(t) for t in s.tables]} for s in db.schemas],
    }


def _database_dialect(d: dict[str, Any]) -> Dialect:
    """Dialekt aus der Datei; ältere Dateien führen MariaDB-Datenbanken noch als MySQL."""
    dialect = Dialect(d["dialect"])
    if dialect is Dialect.MYSQL and "mariadb" in (d.get("server_version") or "").lower():
        return Dialect.MARIADB
    return dialect


def _database_from_dict(d: dict[str, Any]) -> Database:
    return Database(
        name=d["name"],
        dialect=_database_dialect(d),
        schemas=[Schema(s.get("name"), [_table_from_dict(t) for t in s.get("tables", [])]) for s in d.get("schemas", [])],
        connection_profile=d.get("connection"),
        server_version=d.get("server_version"),
        charset=d.get("charset"),
        collation=d.get("collation"),
        modeled=bool(d.get("modeled", False)),
    )


def _table_to_dict(t: Table) -> dict[str, Any]:
    data = _table_body_to_dict(t)
    if t.checks:  # nur wenn vorhanden, damit Dateien ohne Checks unverändert bleiben
        data["checks"] = [{"name": c.name, "expression": c.expression, "columns": list(c.columns)} for c in t.checks]
    if t.is_view:  # nur bei Views, damit Dateien ohne Views unverändert bleiben
        data["kind"] = t.kind.value
        data["definition"] = t.definition
        data["view_dependencies"] = [_ref_to_dict(r) for r in t.view_dependencies]
    return data


def _table_body_to_dict(t: Table) -> dict[str, Any]:
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
        kind=TableKind(d.get("kind", TableKind.TABLE.value)),
        definition=d.get("definition"),
        view_dependencies=[_ref_from_dict(r) for r in d.get("view_dependencies", [])],
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
        checks=[CheckConstraint(c["name"], c["expression"], tuple(c.get("columns", []))) for c in d.get("checks", [])],
    )


def _ref_to_dict(ref: TableRef) -> dict[str, Any]:
    return {"database": ref.database, "schema": ref.schema, "table": ref.table}


def _ref_from_dict(d: dict[str, Any]) -> TableRef:
    return TableRef(d["database"], d.get("schema"), d["table"])


# ---------------------------------------------------------------------- Layout


def _layout_to_dict(layout: Layout) -> dict[str, Any]:
    databases = []
    schema_dbs = {db for db, _ in layout.schema_frames}
    for name in sorted(set(layout.database_colors) | set(layout.frames) | schema_dbs):
        entry: dict[str, Any] = {"name": name}
        if name in layout.database_colors:
            entry["color"] = layout.database_colors[name]
        if name in layout.frames:
            entry["frame"] = [round(v, 1) for v in layout.frames[name]]
        schemas = sorted((s, f) for (db, s), f in layout.schema_frames.items() if db == name)
        if schemas:
            entry["schemas"] = [{"name": s, "frame": [round(v, 1) for v in f]} for s, f in schemas]
        databases.append(entry)
    data = {
        "notation": layout.notation,
        "scale": layout.scale,
        "display": {
            "columns": layout.display.columns.value,
            "types": layout.display.show_types,
            "indexes": layout.display.show_indexes,
            "comments": layout.display.show_comments,
        },
        "databases": databases,
        "entities": [
            {**_ref_to_dict(ref), "x": round(x, 1), "y": round(y, 1)}
            for ref, (x, y) in sorted(layout.positions.items(), key=lambda kv: _sort_key(kv[0]))
        ],
    }
    if layout.notes:  # nur wenn vorhanden – ältere Dateien bleiben unverändert
        data["notes"] = [
            {
                "id": n.id,
                "text": n.text,
                "frame": [round(v, 1) for v in n.frame],
                "entity": _ref_to_dict(n.entity) if n.entity else None,
            }
            for n in layout.notes
        ]
    return data


def _sort_key(ref: TableRef) -> tuple[str, str, str]:
    """Stabile Reihenfolge in der Datei (schema darf None sein)."""
    return (ref.database, ref.schema or "", ref.table)


def _layout_from_dict(d: dict[str, Any]) -> Layout:
    return Layout(
        positions={_ref_from_dict(e): (float(e["x"]), float(e["y"])) for e in d.get("entities", [])},
        database_colors={db["name"]: int(db["color"]) for db in d.get("databases", []) if "color" in db},
        frames={db["name"]: _frame(db["frame"]) for db in d.get("databases", []) if db.get("frame")},
        schema_frames={
            (db["name"], s["name"]): _frame(s["frame"])
            for db in d.get("databases", [])
            for s in db.get("schemas", [])
            if s.get("frame")
        },
        scale=min(max(float(d.get("scale", 1.0)), MIN_SCALE), MAX_SCALE),
        notation=d.get("notation", "crowsfoot"),
        display=_display_from_dict(d.get("display", {})),
        notes=[
            Note(str(n["id"]), str(n.get("text", "")), _frame(n["frame"]),
                 _ref_from_dict(n["entity"]) if n.get("entity") else None)
            for n in d.get("notes", [])
        ],
    )


def _display_from_dict(d: dict[str, Any]) -> DisplayOptions:
    default = DisplayOptions()
    return DisplayOptions(
        columns=ColumnMode(d.get("columns", default.columns.value)),
        show_types=bool(d.get("types", default.show_types)),
        show_indexes=bool(d.get("indexes", default.show_indexes)),
        show_comments=bool(d.get("comments", default.show_comments)),
    )


def _frame(values: list[Any]) -> Frame:
    x, y, w, h = (float(v) for v in values)
    return (x, y, w, h)
