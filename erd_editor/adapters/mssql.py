"""SQL-Server-Adapter: liest ein Schema über die `sys.*`-Katalogsichten.

Aufbau wie bei den anderen Adaptern:
- `MssqlAdapter` führt die Abfragen aus (pyodbc).
- `build_database()` baut aus den Ergebniszeilen das neutrale Modell –
  ohne Datenbankverbindung, daher direkt testbar.

Besonderheiten von SQL Server:
- Anmeldung per Windows (`options["auth"] = "windows"`, Standard) oder
  SQL-Login (Benutzer und Passwort).
- Jede Datenbank bekommt eine eigene Verbindung; Fremdschlüssel gibt es
  nur innerhalb einer Datenbank, aber über Schemagrenzen hinweg.
- Kommentare stehen in den Extended Properties (`MS_Description`).
- Datentypen werden aus Name, Länge, Genauigkeit und Skala nativ
  zusammengesetzt (`nvarchar(100)`, `varchar(max)`, `decimal(10,2)`).
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

DEFAULT_PORT = 1433
PREFERRED_DRIVERS = ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server", "SQL Server")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ".", "(local)"}

SQL_DATABASES = """
    SELECT name
    FROM sys.databases
    WHERE database_id > 4                 -- ohne master, tempdb, model, msdb
      AND state_desc = 'ONLINE'
      AND HAS_DBACCESS(name) = 1
    ORDER BY name
"""

SQL_DATABASE_INFO = """
    SELECT collation_name AS collation
    FROM sys.databases
    WHERE name = DB_NAME()
"""

SQL_SERVER_VERSION = """
    SELECT CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(50)) AS version
"""

SQL_SCHEMAS = """
    SELECT name AS schema_name
    FROM sys.schemas
    WHERE schema_id < 16384               -- ohne Rollen-Schemas (db_owner …)
      AND name NOT IN ('sys', 'INFORMATION_SCHEMA', 'guest')
    ORDER BY name
"""

# U = Tabelle, V = View
SQL_TABLES = """
    SELECT s.name AS schema_name,
           t.name AS table_name,
           CAST(ep.value AS nvarchar(max)) AS comment,
           RTRIM(t.type) AS kind,
           m.definition AS definition
    FROM sys.objects t
    JOIN sys.schemas s ON s.schema_id = t.schema_id
    LEFT JOIN sys.sql_modules m ON m.object_id = t.object_id
    LEFT JOIN sys.extended_properties ep
           ON ep.class = 1 AND ep.major_id = t.object_id AND ep.minor_id = 0 AND ep.name = 'MS_Description'
    WHERE t.is_ms_shipped = 0 AND t.type IN ('U', 'V')
    ORDER BY s.name, t.name
"""

# Tabellen/Views derselben Datenbank, die eine View benutzt
SQL_VIEW_USAGE = """
    SELECT DISTINCT s.name AS schema_name,
           v.name AS view_name,
           rs.name AS referenced_schema,
           r.name AS referenced_table
    FROM sys.sql_expression_dependencies d
    JOIN sys.views v    ON v.object_id = d.referencing_id
    JOIN sys.schemas s  ON s.schema_id = v.schema_id
    JOIN sys.objects r  ON r.object_id = d.referenced_id
    JOIN sys.schemas rs ON rs.schema_id = r.schema_id
    WHERE d.referenced_database_name IS NULL
      AND r.type IN ('U', 'V')
      AND v.is_ms_shipped = 0
    ORDER BY 1, 2, 3, 4
"""

SQL_COLUMNS = """
    SELECT s.name          AS schema_name,
           t.name          AS table_name,
           c.name          AS column_name,
           ty.name         AS type_name,
           ty.is_user_defined AS is_user_type,
           c.max_length    AS max_length,
           c.precision     AS precision,
           c.scale         AS scale,
           c.is_nullable   AS nullable,
           c.is_identity   AS is_identity,
           dc.definition   AS column_default,
           CAST(ep.value AS nvarchar(max)) AS comment
    FROM sys.columns c
    JOIN sys.objects t  ON t.object_id = c.object_id AND t.type IN ('U', 'V')
    JOIN sys.schemas s  ON s.schema_id = t.schema_id
    JOIN sys.types ty   ON ty.user_type_id = c.user_type_id
    LEFT JOIN sys.default_constraints dc ON dc.object_id = c.default_object_id
    LEFT JOIN sys.extended_properties ep
           ON ep.class = 1 AND ep.major_id = c.object_id AND ep.minor_id = c.column_id AND ep.name = 'MS_Description'
    WHERE t.is_ms_shipped = 0
    ORDER BY s.name, t.name, c.column_id
"""

SQL_INDEXES = """
    SELECT s.name          AS schema_name,
           t.name          AS table_name,
           i.name          AS index_name,
           i.is_primary_key AS is_primary,
           i.is_unique     AS is_unique,
           i.type_desc     AS type_desc,
           ic.key_ordinal  AS seq,
           c.name          AS column_name
    FROM sys.indexes i
    JOIN sys.tables t         ON t.object_id = i.object_id
    JOIN sys.schemas s        ON s.schema_id = t.schema_id
    JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
    JOIN sys.columns c        ON c.object_id = ic.object_id AND c.column_id = ic.column_id
    WHERE t.is_ms_shipped = 0
      AND i.type > 0                      -- ohne Heap
      AND i.is_hypothetical = 0
      AND ic.is_included_column = 0       -- INCLUDE-Spalten sind keine Schlüsselspalten
    ORDER BY s.name, t.name, i.name, ic.key_ordinal, ic.index_column_id
"""

SQL_FOREIGN_KEYS = """
    SELECT s.name   AS schema_name,
           t.name   AS table_name,
           fk.name  AS constraint_name,
           fkc.constraint_column_id AS seq,
           pc.name  AS column_name,
           rs.name  AS referenced_schema,
           rt.name  AS referenced_table,
           rc.name  AS referenced_column,
           fk.update_referential_action_desc AS update_rule,
           fk.delete_referential_action_desc AS delete_rule
    FROM sys.foreign_keys fk
    JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
    JOIN sys.tables t   ON t.object_id = fk.parent_object_id
    JOIN sys.schemas s  ON s.schema_id = t.schema_id
    JOIN sys.columns pc ON pc.object_id = fkc.parent_object_id AND pc.column_id = fkc.parent_column_id
    JOIN sys.tables rt  ON rt.object_id = fk.referenced_object_id
    JOIN sys.schemas rs ON rs.schema_id = rt.schema_id
    JOIN sys.columns rc ON rc.object_id = fkc.referenced_object_id AND rc.column_id = fkc.referenced_column_id
    WHERE t.is_ms_shipped = 0
    ORDER BY s.name, t.name, fk.name, fkc.constraint_column_id
"""

_LENGTH_TYPES = {"char", "varchar", "binary", "varbinary"}
_UNICODE_TYPES = {"nchar", "nvarchar"}  # max_length in Bytes, 2 je Zeichen
_PRECISION_SCALE_TYPES = {"decimal", "numeric"}
_SCALE_TYPES = {"datetime2", "time", "datetimeoffset"}


def format_type(name: str, max_length: int, precision: int, scale: int, user_type: bool = False) -> str:
    """Datentyp in SQL-Server-Schreibweise, z. B. `nvarchar(100)`, `varchar(max)`, `decimal(10,2)`."""
    if user_type:
        return name
    if name in _LENGTH_TYPES or name in _UNICODE_TYPES:
        if max_length == -1:
            return f"{name}(max)"
        length = max_length // 2 if name in _UNICODE_TYPES else max_length
        return f"{name}({length})"
    if name in _PRECISION_SCALE_TYPES:
        return f"{name}({precision},{scale})"
    if name in _SCALE_TYPES:
        return f"{name}({scale})"
    return name


# Hauptversion → Produktname
PRODUCT_YEARS = {"17": "2025", "16": "2022", "15": "2019", "14": "2017", "13": "2016", "12": "2014", "11": "2012"}


def product_version(version: str | None) -> str | None:
    """`17.0.1145.4` → `2025 (17.0.1145.4)`; unbekannte Versionen bleiben unverändert."""
    if not version:
        return version
    year = PRODUCT_YEARS.get(version.split(".", 1)[0])
    return f"{year} ({version})" if year else version


def _text(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value)
    return value or None


def _rule(value: Any) -> str | None:
    """`NO_ACTION` → `NO ACTION`."""
    text = _text(value)
    return text.replace("_", " ") if text else None


def _index_kind(is_primary: Any, is_unique: Any, type_desc: Any) -> IndexKind:
    if is_primary:
        return IndexKind.PRIMARY
    if _text(type_desc) == "SPATIAL":
        return IndexKind.SPATIAL
    return IndexKind.UNIQUE if is_unique else IndexKind.INDEX


# Kommentare/Leerraum, dann CREATE [OR ALTER] VIEW name [(spalten)] [WITH …] AS
_VIEW_HEADER = re.compile(
    r"^(?:\s+|--[^\n]*(?:\n|$)|/\*.*?\*/)*create\s+(?:or\s+alter\s+)?view\s+.+?\bas\b\s*",
    re.IGNORECASE | re.DOTALL,
)


def view_body(module: str | None) -> str | None:
    """SELECT-Teil aus dem Modultext `CREATE VIEW … AS SELECT …` (ohne Semikolon am Ende)."""
    if not module:
        return None
    match = _VIEW_HEADER.match(module)
    body = module[match.end():] if match else module
    return body.strip().removesuffix(";").rstrip() or None


def _strip_parens(definition: str | None) -> str | None:
    """SQL Server speichert Defaults geklammert: `((1))` → `1`, `('')` → `''`."""
    if definition is None:
        return None
    text = definition.strip()
    while len(text) >= 2 and text[0] == "(" and text[-1] == ")" and _balanced(text[1:-1]):
        text = text[1:-1].strip()
    return text


def _balanced(text: str) -> bool:
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def build_database(
    name: str,
    *,
    schemas: Iterable[Row],
    tables: Iterable[Row],
    columns: Iterable[Row],
    indexes: Iterable[Row],
    foreign_keys: Iterable[Row],
    view_usage: Iterable[Row] = (),
    collation: str | None = None,
    server_version: str | None = None,
    connection_profile: str | None = None,
) -> Database:
    """Neutrales Modell aus den Ergebniszeilen der `SQL_*`-Abfragen bauen.

    Schemas ohne Tabellen werden weggelassen.
    """
    by_schema: dict[str, dict[str, Table]] = {r["schema_name"]: {} for r in schemas}
    for row in tables:
        t = Table(row["table_name"], comment=_text(row["comment"]))
        if _text(row.get("kind")) == "V":
            t.kind = TableKind.VIEW
            t.definition = view_body(_text(row.get("definition")))
        by_schema.setdefault(row["schema_name"], {})[row["table_name"]] = t

    def table(row: Row) -> Table | None:
        return by_schema.get(row["schema_name"], {}).get(row["table_name"])

    for row in columns:
        t = table(row)
        if t is None:
            continue
        t.columns.append(
            Column(
                name=row["column_name"],
                data_type=format_type(row["type_name"], int(row["max_length"]), int(row["precision"]),
                                      int(row["scale"]), bool(row["is_user_type"])),
                nullable=bool(row["nullable"]),
                default=_strip_parens(row["column_default"]),
                comment=_text(row["comment"]),
                auto_increment=bool(row["is_identity"]),
            )
        )

    index_parts: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    index_kinds: dict[tuple[str, str, str], IndexKind] = {}
    for row in indexes:
        if table(row) is None:
            continue
        key = (row["schema_name"], row["table_name"], row["index_name"])
        index_kinds.setdefault(key, _index_kind(row["is_primary"], row["is_unique"], row["type_desc"]))
        index_parts[key].append(row["column_name"])
    for key, kind in index_kinds.items():
        by_schema[key[0]][key[1]].indexes.append(Index(key[2], kind, tuple(index_parts[key])))

    fk_rows: dict[tuple[str, str, str], list[Row]] = defaultdict(list)
    for row in foreign_keys:
        if table(row) is not None:
            fk_rows[(row["schema_name"], row["table_name"], row["constraint_name"])].append(row)
    for (schema, table_name, fk_name), rows in fk_rows.items():
        first = rows[0]
        by_schema[schema][table_name].foreign_keys.append(
            ForeignKey(
                name=fk_name,
                columns=tuple(r["column_name"] for r in rows),
                referenced_table=TableRef(name, first["referenced_schema"], first["referenced_table"]),
                referenced_columns=tuple(r["referenced_column"] for r in rows),
                on_update=_rule(first["update_rule"]),
                on_delete=_rule(first["delete_rule"]),
            )
        )

    for row in view_usage:
        view = table({"schema_name": row["schema_name"], "table_name": row["view_name"]})
        if view is not None and view.is_view:
            ref = TableRef(name, row["referenced_schema"], row["referenced_table"])
            if ref not in view.view_dependencies:
                view.view_dependencies.append(ref)

    return Database(
        name=name,
        dialect=Dialect.MSSQL,
        schemas=[Schema(s, list(t.values())) for s, t in by_schema.items() if t],
        connection_profile=connection_profile,
        server_version=server_version,
        charset=None,  # bei SQL Server über die Kollation bestimmt
        collation=collation,
    )


def uses_windows_auth(profile: ConnectionProfile) -> bool:
    return profile.options.get("auth", "windows") == "windows"


def connection_string(profile: ConnectionProfile, password: str | None, database: str | None, driver: str) -> str:
    """ODBC-Verbindungszeichenkette (Passwort nur bei SQL-Login)."""
    server = profile.host + (f",{profile.port}" if profile.port else "")
    parts = [f"DRIVER={{{driver}}}", f"SERVER={server}"]
    if database:
        parts.append(f"DATABASE={{{database}}}")
    if uses_windows_auth(profile):
        parts.append("Trusted_Connection=yes")
    else:
        parts += [f"UID={{{profile.user or ''}}}", f"PWD={{{(password or '').replace('}', '}}')}}}"]
    # Lokale Server haben meist ein selbst ausgestelltes Zertifikat
    trust = profile.options.get("trust_server_certificate", "yes" if profile.host.lower() in LOCAL_HOSTS else "no")
    parts += ["Encrypt=yes", f"TrustServerCertificate={trust}"]
    return ";".join(parts)


class MssqlAdapter(SchemaAdapter):
    """Liest Datenbanken eines SQL Servers."""

    dialect = Dialect.MSSQL
    DEFAULT_PORT = DEFAULT_PORT

    def __init__(self, profile: ConnectionProfile, password: str | None = None) -> None:
        super().__init__(profile, password)
        self._conn = None

    @staticmethod
    def driver() -> str:
        import pyodbc

        installed = set(pyodbc.drivers())
        for name in PREFERRED_DRIVERS:
            if name in installed:
                return name
        raise AdapterError("Kein ODBC-Treiber für SQL Server gefunden (empfohlen: ODBC Driver 18 for SQL Server)")

    def _open(self, database: str | None):
        import pyodbc

        try:
            return pyodbc.connect(connection_string(self.profile, self._password, database, self.driver()),
                                  timeout=10, autocommit=True)
        except pyodbc.Error as exc:
            raise AdapterError(f"Verbindung zu {self.profile.host} fehlgeschlagen: {exc}") from exc

    def connect(self) -> None:
        self._conn = self._open(None)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @staticmethod
    def _query(conn, sql: str) -> list[Row]:
        cursor = conn.cursor()
        try:
            cursor.execute(sql)
            names = [d[0] for d in cursor.description]
            return [dict(zip(names, row)) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def _require_connection(self):
        if self._conn is None:
            raise AdapterError("Keine Verbindung – zuerst connect() aufrufen")
        return self._conn

    def list_databases(self) -> list[str]:
        return [r["name"] for r in self._query(self._require_connection(), SQL_DATABASES)]

    def read_database(self, name: str) -> Database:
        conn0 = self._require_connection()
        if name not in self.list_databases():
            raise AdapterError(f"Datenbank {name!r} nicht gefunden oder kein Zugriff")
        version = self._query(conn0, SQL_SERVER_VERSION)[0]["version"]
        conn = self._open(name)  # eigene Verbindung je Datenbank
        try:
            info = self._query(conn, SQL_DATABASE_INFO)[0]
            return build_database(
                name,
                schemas=self._query(conn, SQL_SCHEMAS),
                tables=self._query(conn, SQL_TABLES),
                columns=self._query(conn, SQL_COLUMNS),
                indexes=self._query(conn, SQL_INDEXES),
                foreign_keys=self._query(conn, SQL_FOREIGN_KEYS),
                view_usage=self._query(conn, SQL_VIEW_USAGE),
                collation=_text(info["collation"]),
                server_version=product_version(_text(version)),
                connection_profile=self.profile.name,
            )
        except AdapterError:
            raise
        except Exception as exc:  # pyodbc.Error, ohne den Import zu erzwingen
            raise AdapterError(f"Fehler beim Lesen von {name!r}: {exc}") from exc
        finally:
            conn.close()
