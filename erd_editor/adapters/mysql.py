"""MySQL-Adapter: liest ein Schema über `information_schema`.

Aufbau:
- `MySqlAdapter` führt die Abfragen aus (PyMySQL).
- `build_database()` baut aus den Ergebniszeilen das neutrale Modell –
  ohne Datenbankverbindung, daher direkt testbar.

Alle Abfragen vergeben eigene Spaltenaliase in Kleinbuchstaben, weil MySQL 8
die Spaltennamen von `information_schema` sonst teils groß zurückgibt.
"""

from __future__ import annotations

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
           TABLE_COMMENT AS comment
    FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'
    ORDER BY TABLE_NAME
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
    charset: str | None = None,
    collation: str | None = None,
    server_version: str | None = None,
    connection_profile: str | None = None,
) -> Database:
    """Neutrales Modell aus den Ergebniszeilen der `SQL_*`-Abfragen bauen.

    Die Zeilen müssen so sortiert sein wie in den Abfragen (ORDER BY).
    Funktionsindexe (MySQL 8, ohne Spaltennamen) werden ohne die
    Ausdrucksteile übernommen; besteht ein Index nur aus Ausdrücken,
    entfällt er.
    """
    by_name: dict[str, Table] = {}
    for row in tables:
        table_name = _text(row["table_name"])
        by_name[table_name] = Table(table_name, comment=_text(row["comment"]))

    for row in columns:
        table = by_name.get(_text(row["table_name"]))
        if table is None:  # z. B. Spalten einer View
            continue
        extra = (_text(row["extra"]) or "").lower()
        table.columns.append(
            Column(
                name=_text(row["column_name"]),
                data_type=_text(row["column_type"]),
                nullable=_text(row["is_nullable"]) == "YES",
                # leerer String ist ein echter Default (DEFAULT ''), daher nicht _text()
                default=_decode(row["column_default"]),
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
            charset=_text(info[0]["charset"]),
            collation=_text(info[0]["collation"]),
            server_version=self.server_version(),
            connection_profile=self.profile.name,
        )
