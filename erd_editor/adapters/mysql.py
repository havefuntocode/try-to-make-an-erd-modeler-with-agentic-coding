"""MySQL-Adapter: liest ein Schema über `information_schema`.

Aufbau:
- `MySqlAdapter` führt die Abfragen aus (PyMySQL).
- `build_database()` baut aus den Ergebniszeilen das neutrale Modell –
  ohne Datenbankverbindung, daher direkt testbar.

Alle Abfragen vergeben eigene Spaltenaliase in Kleinbuchstaben, weil MySQL 8
die Spaltennamen von `information_schema` sonst teils groß zurückgibt.

MariaDB wird über denselben Adapter gelesen. Zwei Abweichungen werden an
MySQL 8 angeglichen (`mariadb=True`):
- Ganzzahltypen tragen eine Anzeigebreite (`int(11)`), MySQL 8 nicht (`int`).
- `COLUMN_DEFAULT` liefert Literale gequotet (`'abc'`, `NULL` als Text),
  MySQL den nackten Wert bzw. SQL-NULL.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from erd_editor.adapters.base import AdapterError, SchemaAdapter
from erd_editor.model import (
    Column,
    ConnectionProfile,
    Database,
    Dialect,
    ForeignKey,
    Index,
    IndexKind,
    Schema,
    Table,
    TableKind,
    TableRef,
)

Row = Mapping[str, Any]

SYSTEM_DATABASES = frozenset({"information_schema", "mysql", "performance_schema", "sys"})

SQL_DATABASES = """
    SELECT SCHEMA_NAME AS name
    FROM information_schema.SCHEMATA
    ORDER BY SCHEMA_NAME
"""

SQL_DATABASE_INFO = """
    SELECT DEFAULT_CHARACTER_SET_NAME AS charset,
           DEFAULT_COLLATION_NAME     AS collation
    FROM information_schema.SCHEMATA
    WHERE SCHEMA_NAME = %s
"""

SQL_TABLES = """
    SELECT TABLE_NAME    AS table_name,
           TABLE_COMMENT AS comment,
           TABLE_TYPE    AS table_type
    FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = %s AND TABLE_TYPE IN ('BASE TABLE', 'VIEW')
    ORDER BY TABLE_NAME
"""

SQL_VIEWS = """
    SELECT TABLE_NAME      AS view_name,
           VIEW_DEFINITION AS definition
    FROM information_schema.VIEWS
    WHERE TABLE_SCHEMA = %s
"""

# MySQL 8.0.13+; MariaDB kennt die Tabelle nicht (dann aus dem SQL-Text)
SQL_VIEW_USAGE = """
    SELECT VIEW_NAME    AS view_name,
           TABLE_SCHEMA AS referenced_schema,
           TABLE_NAME   AS referenced_table
    FROM information_schema.VIEW_TABLE_USAGE
    WHERE VIEW_SCHEMA = %s
    ORDER BY VIEW_NAME, TABLE_SCHEMA, TABLE_NAME
"""

SQL_COLUMNS = """
    SELECT TABLE_NAME     AS table_name,
           COLUMN_NAME    AS column_name,
           COLUMN_TYPE    AS column_type,
           IS_NULLABLE    AS is_nullable,
           COLUMN_DEFAULT AS column_default,
           COLUMN_COMMENT AS comment,
           EXTRA          AS extra
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = %s
    ORDER BY TABLE_NAME, ORDINAL_POSITION
"""

SQL_INDEXES = """
    SELECT TABLE_NAME   AS table_name,
           INDEX_NAME   AS index_name,
           NON_UNIQUE   AS non_unique,
           SEQ_IN_INDEX AS seq,
           COLUMN_NAME  AS column_name,
           INDEX_TYPE   AS index_type
    FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = %s
    ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX
"""

SQL_FOREIGN_KEYS = """
    SELECT kcu.TABLE_NAME              AS table_name,
           kcu.CONSTRAINT_NAME         AS constraint_name,
           kcu.ORDINAL_POSITION        AS seq,
           kcu.COLUMN_NAME             AS column_name,
           kcu.REFERENCED_TABLE_SCHEMA AS referenced_schema,
           kcu.REFERENCED_TABLE_NAME   AS referenced_table,
           kcu.REFERENCED_COLUMN_NAME  AS referenced_column,
           rc.UPDATE_RULE              AS update_rule,
           rc.DELETE_RULE              AS delete_rule
    FROM information_schema.KEY_COLUMN_USAGE kcu
    JOIN information_schema.REFERENTIAL_CONSTRAINTS rc
      ON rc.CONSTRAINT_SCHEMA = kcu.CONSTRAINT_SCHEMA
     AND rc.CONSTRAINT_NAME   = kcu.CONSTRAINT_NAME
     AND rc.TABLE_NAME        = kcu.TABLE_NAME
    WHERE kcu.TABLE_SCHEMA = %s
      AND kcu.REFERENCED_TABLE_NAME IS NOT NULL
    ORDER BY kcu.TABLE_NAME, kcu.CONSTRAINT_NAME, kcu.ORDINAL_POSITION
"""


def _decode(value: Any) -> str | None:
    """Wert als Text; Bytes werden dekodiert, None bleibt None."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return str(value)


def _text(value: Any) -> str | None:
    """Wie `_decode`, aber leere Texte werden zu None (z. B. fehlende Kommentare)."""
    return _decode(value) or None


_INT_DISPLAY_WIDTH = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)", re.IGNORECASE)


def is_mariadb(server_version: str | None) -> bool:
    return "mariadb" in (server_version or "").lower()


def normalize_mariadb_type(column_type: str) -> str:
    """Anzeigebreite von Ganzzahltypen entfernen, wie MySQL 8 sie ausgibt.

    `tinyint(1)` (BOOLEAN) und `ZEROFILL`-Spalten behält auch MySQL 8 bei.
    """
    if "zerofill" in column_type.lower() or column_type.lower().startswith("tinyint(1)"):
        return column_type
    return _INT_DISPLAY_WIDTH.sub(lambda m: m.group(1), column_type)


def normalize_mariadb_default(value: str | None) -> str | None:
    """MariaDB-Default (gequotetes Literal, `NULL` als Text) wie bei MySQL 8."""
    if value is None or value == "NULL":
        return None
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


# `db`.`tabelle` direkt hinter FROM/JOIN (ggf. nach öffnenden Klammern)
_FROM_OBJECT = re.compile(r"\b(?:from|join)\s*[(\s]*`((?:[^`]|``)+)`\.`((?:[^`]|``)+)`", re.IGNORECASE)


def view_usage_from_definition(definition: str) -> list[tuple[str, str]]:
    """(Datenbank, Tabelle) aller Objekte hinter FROM/JOIN im View-Text.

    MySQL und MariaDB speichern View-Definitionen mit voll qualifizierten
    Objektnamen (`` `db`.`tabelle` ``). Ersatz für `VIEW_TABLE_USAGE`, das
    MariaDB nicht kennt.
    """
    found: list[tuple[str, str]] = []
    for match in _FROM_OBJECT.finditer(definition):
        pair = (match.group(1).replace("``", "`"), match.group(2).replace("``", "`"))
        if pair not in found:
            found.append(pair)
    return found


def strip_own_database(definition: str, database: str) -> str:
    """Eigenen Datenbanknamen aus dem View-Text entfernen (portabel für den SQL-Export)."""
    quoted = "`" + database.replace("`", "``") + "`."
    return definition.replace(quoted, "")


def _index_kind(index_name: str, non_unique: Any, index_type: str | None) -> IndexKind:
    if index_name == "PRIMARY":
        return IndexKind.PRIMARY
    match (index_type or "").upper():
        case "FULLTEXT":
            return IndexKind.FULLTEXT
        case "SPATIAL":
            return IndexKind.SPATIAL
    return IndexKind.INDEX if int(non_unique) else IndexKind.UNIQUE


def build_database(
    name: str,
    *,
    tables: Iterable[Row],
    columns: Iterable[Row],
    indexes: Iterable[Row],
    foreign_keys: Iterable[Row],
    views: Iterable[Row] = (),
    view_usage: Iterable[Row] | None = None,
    charset: str | None = None,
    collation: str | None = None,
    server_version: str | None = None,
    connection_profile: str | None = None,
    mariadb: bool | None = None,
) -> Database:
    """Neutrales Modell aus den Ergebniszeilen der `SQL_*`-Abfragen bauen.

    Die Zeilen müssen so sortiert sein wie in den Abfragen (ORDER BY).
    Funktionsindexe (MySQL 8, ohne Spaltennamen) werden ohne die
    Ausdrucksteile übernommen; besteht ein Index nur aus Ausdrücken,
    entfällt er.

    `mariadb=None` erkennt MariaDB an `server_version`.
    """
    if mariadb is None:
        mariadb = is_mariadb(server_version)
    by_name: dict[str, Table] = {}
    for row in tables:
        table_name = _text(row["table_name"])
        if (_text(row.get("table_type")) or "BASE TABLE") == "VIEW":
            # Views haben keinen eigenen Kommentar; MySQL meldet dort „VIEW“
            by_name[table_name] = Table(table_name, kind=TableKind.VIEW)
        else:
            by_name[table_name] = Table(table_name, comment=_text(row["comment"]))

    for row in views:
        view = by_name.get(_text(row["view_name"]))
        if view is not None and view.is_view:
            text = _decode(row["definition"]) or ""
            if view_usage is None:  # MariaDB: Abhängigkeiten aus dem Text
                for db_name, obj in view_usage_from_definition(text):
                    ref = TableRef(db_name, None, obj)
                    if ref not in view.view_dependencies:
                        view.view_dependencies.append(ref)
            view.definition = strip_own_database(text, name) or None
    for row in view_usage or ():
        view = by_name.get(_text(row["view_name"]))
        if view is not None and view.is_view:
            ref = TableRef(_text(row["referenced_schema"]), None, _text(row["referenced_table"]))
            if ref not in view.view_dependencies:
                view.view_dependencies.append(ref)

    for row in columns:
        table = by_name.get(_text(row["table_name"]))
        if table is None:  # z. B. Spalten einer View
            continue
        extra = (_text(row["extra"]) or "").lower()
        data_type = _text(row["column_type"])
        # leerer String ist ein echter Default (DEFAULT ''), daher nicht _text()
        default = _decode(row["column_default"])
        if mariadb:
            data_type = normalize_mariadb_type(data_type)
            default = normalize_mariadb_default(default)
        table.columns.append(
            Column(
                name=_text(row["column_name"]),
                data_type=data_type,
                nullable=_text(row["is_nullable"]) == "YES",
                default=default,
                comment=_text(row["comment"]),
                auto_increment="auto_increment" in extra,
            )
        )

    index_parts: dict[tuple[str, str], list[str]] = defaultdict(list)
    index_kinds: dict[tuple[str, str], IndexKind] = {}
    for row in indexes:
        key = (_text(row["table_name"]), _text(row["index_name"]))
        if key[0] not in by_name:
            continue
        index_kinds.setdefault(key, _index_kind(key[1], row["non_unique"], _text(row["index_type"])))
        column_name = _text(row["column_name"])
        if column_name is not None:
            index_parts[key].append(column_name)
    for (table_name, index_name), kind in index_kinds.items():
        cols = tuple(index_parts.get((table_name, index_name), ()))
        if cols:
            by_name[table_name].indexes.append(Index(index_name, kind, cols))

    fk_rows: dict[tuple[str, str], list[Row]] = defaultdict(list)
    for row in foreign_keys:
        key = (_text(row["table_name"]), _text(row["constraint_name"]))
        if key[0] in by_name:
            fk_rows[key].append(row)
    for (table_name, fk_name), rows in fk_rows.items():
        first = rows[0]
        by_name[table_name].foreign_keys.append(
            ForeignKey(
                name=fk_name,
                columns=tuple(_text(r["column_name"]) for r in rows),
                referenced_table=TableRef(
                    _text(first["referenced_schema"]), None, _text(first["referenced_table"])
                ),
                referenced_columns=tuple(_text(r["referenced_column"]) for r in rows),
                on_update=_text(first["update_rule"]),
                on_delete=_text(first["delete_rule"]),
            )
        )

    return Database(
        name=name,
        dialect=Dialect.MYSQL,
        schemas=[Schema(None, list(by_name.values()))],
        connection_profile=connection_profile,
        server_version=server_version,
        charset=charset,
        collation=collation,
    )


class MySqlAdapter(SchemaAdapter):
    """Liest Datenbanken eines MySQL-Servers."""

    dialect = Dialect.MYSQL
    DEFAULT_PORT = 3306

    def __init__(self, profile: ConnectionProfile, password: str | None = None) -> None:
        super().__init__(profile, password)
        self._conn = None

    def connect(self) -> None:
        import pymysql
        from pymysql.cursors import DictCursor

        try:
            self._conn = pymysql.connect(
                host=self.profile.host,
                port=self.profile.port or self.DEFAULT_PORT,
                user=self.profile.user,
                password=self._password or "",
                charset="utf8mb4",
                cursorclass=DictCursor,
                connect_timeout=10,
            )
        except pymysql.MySQLError as exc:
            raise AdapterError(f"Verbindung zu {self.profile.host} fehlgeschlagen: {exc}") from exc

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _query(self, sql: str, *params: Any) -> list[Row]:
        if self._conn is None:
            raise AdapterError("Keine Verbindung – zuerst connect() aufrufen")
        with self._conn.cursor() as cursor:
            cursor.execute(sql, params or None)
            return list(cursor.fetchall())

    def server_version(self) -> str:
        return _text(self._query("SELECT VERSION() AS version")[0]["version"])

    def list_databases(self) -> list[str]:
        names = (_text(r["name"]) for r in self._query(SQL_DATABASES))
        return [n for n in names if n.lower() not in SYSTEM_DATABASES]

    def _view_usage(self, name: str) -> list[Row] | None:
        """`VIEW_TABLE_USAGE` – None, wenn der Server sie nicht kennt (MariaDB, MySQL < 8.0.13)."""
        import pymysql

        try:
            return self._query(SQL_VIEW_USAGE, name)
        except pymysql.MySQLError:
            return None

    def read_database(self, name: str) -> Database:
        info = self._query(SQL_DATABASE_INFO, name)
        if not info:
            raise AdapterError(f"Datenbank {name!r} nicht gefunden")
        return build_database(
            name,
            tables=self._query(SQL_TABLES, name),
            columns=self._query(SQL_COLUMNS, name),
            indexes=self._query(SQL_INDEXES, name),
            foreign_keys=self._query(SQL_FOREIGN_KEYS, name),
            views=self._query(SQL_VIEWS, name),
            view_usage=self._view_usage(name),
            charset=_text(info[0]["charset"]),
            collation=_text(info[0]["collation"]),
            server_version=self.server_version(),
            connection_profile=self.profile.name,
        )
