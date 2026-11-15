"""PostgreSQL-Adapter: liest ein Schema über `pg_catalog`.

Aufbau wie beim MySQL-Adapter:
- `PostgresAdapter` führt die Abfragen aus (psycopg 3).
- `build_database()` baut aus den Ergebniszeilen das neutrale Modell –
  ohne Datenbankverbindung, daher direkt testbar.

Besonderheiten von PostgreSQL:
- Jede Datenbank braucht eine eigene Verbindung; Fremdschlüssel gibt es
  nur innerhalb einer Datenbank (aber über Schemagrenzen hinweg).
- Eine Datenbank enthält mehrere Schemas (`public`, …) – im Modell als
  `Schema`-Ebene abgebildet.
- `pg_catalog` statt `information_schema`, weil nur dort Kommentare,
  Indexe und die Spaltenreihenfolge in Fremdschlüsseln vollständig sind.
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
    TableKind,
    TableRef,
)

Row = Mapping[str, Any]

DEFAULT_PORT = 5432
MAINTENANCE_DATABASE = "postgres"  # für die Anmeldung und die Datenbankliste

# Schemas, die zum System gehören
SYSTEM_SCHEMA_FILTER = """
    n.nspname NOT IN ('pg_catalog', 'information_schema')
    AND n.nspname NOT LIKE 'pg\\_toast%'
    AND n.nspname NOT LIKE 'pg\\_temp\\_%'
"""

SQL_DATABASES = """
    SELECT datname AS name
    FROM pg_database
    WHERE NOT datistemplate AND datallowconn
    ORDER BY datname
"""

SQL_DATABASE_INFO = """
    SELECT pg_encoding_to_char(encoding) AS charset,
           datcollate                   AS collation
    FROM pg_database
    WHERE datname = current_database()
"""

SQL_SCHEMAS = f"""
    SELECT n.nspname AS schema_name
    FROM pg_namespace n
    WHERE {SYSTEM_SCHEMA_FILTER}
    ORDER BY n.nspname
"""

# r = Tabelle, p = partitionierte Tabelle, v = View; Partitionen selbst werden ausgelassen
SQL_TABLES = f"""
    SELECT n.nspname                        AS schema_name,
           c.relname                        AS table_name,
           obj_description(c.oid, 'pg_class') AS comment,
           c.relkind                        AS kind,
           CASE WHEN c.relkind = 'v' THEN pg_get_viewdef(c.oid, true) END AS definition
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r', 'p', 'v')
      AND NOT c.relispartition
      AND {SYSTEM_SCHEMA_FILTER}
    ORDER BY n.nspname, c.relname
"""

SQL_COLUMNS = f"""
    SELECT n.nspname                             AS schema_name,
           c.relname                             AS table_name,
           a.attname                             AS column_name,
           format_type(a.atttypid, a.atttypmod)  AS data_type,
           NOT a.attnotnull                      AS nullable,
           pg_get_expr(d.adbin, d.adrelid)       AS column_default,
           col_description(c.oid, a.attnum)      AS comment,
           a.attidentity                         AS identity,
           a.attgenerated                        AS generated
    FROM pg_attribute a
    JOIN pg_class c      ON c.oid = a.attrelid
    JOIN pg_namespace n  ON n.oid = c.relnamespace
    LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
    WHERE c.relkind IN ('r', 'p', 'v')
      AND NOT c.relispartition
      AND a.attnum > 0
      AND NOT a.attisdropped
      AND {SYSTEM_SCHEMA_FILTER}
    ORDER BY n.nspname, c.relname, a.attnum
"""

# Eine Zeile je Indexteil; Ausdrucksteile haben attnum = 0 und keinen Spaltennamen
SQL_INDEXES = f"""
    SELECT n.nspname        AS schema_name,
           t.relname        AS table_name,
           i.relname        AS index_name,
           ix.indisprimary  AS is_primary,
           ix.indisunique   AS is_unique,
           am.amname        AS method,
           k.ord            AS seq,
           a.attname        AS column_name
    FROM pg_index ix
    JOIN pg_class t      ON t.oid = ix.indrelid
    JOIN pg_class i      ON i.oid = ix.indexrelid
    JOIN pg_am am        ON am.oid = i.relam
    JOIN pg_namespace n  ON n.oid = t.relnamespace
    CROSS JOIN LATERAL unnest(ix.indkey::int2[]) WITH ORDINALITY AS k(attnum, ord)
    LEFT JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
    WHERE t.relkind IN ('r', 'p')
      AND NOT t.relispartition
      AND k.ord <= ix.indnkeyatts
      AND {SYSTEM_SCHEMA_FILTER}
    ORDER BY n.nspname, t.relname, i.relname, k.ord
"""

SQL_FOREIGN_KEYS = f"""
    SELECT n.nspname     AS schema_name,
           t.relname     AS table_name,
           con.conname   AS constraint_name,
           k.ord         AS seq,
           a.attname     AS column_name,
           rn.nspname    AS referenced_schema,
           rt.relname    AS referenced_table,
           ra.attname    AS referenced_column,
           con.confupdtype AS update_rule,
           con.confdeltype AS delete_rule
    FROM pg_constraint con
    JOIN pg_class t      ON t.oid = con.conrelid
    JOIN pg_namespace n  ON n.oid = t.relnamespace
    JOIN pg_class rt     ON rt.oid = con.confrelid
    JOIN pg_namespace rn ON rn.oid = rt.relnamespace
    CROSS JOIN LATERAL unnest(con.conkey, con.confkey) WITH ORDINALITY AS k(attnum, ref_attnum, ord)
    JOIN pg_attribute a  ON a.attrelid = con.conrelid AND a.attnum = k.attnum
    JOIN pg_attribute ra ON ra.attrelid = con.confrelid AND ra.attnum = k.ref_attnum
    WHERE con.contype = 'f'
      AND NOT t.relispartition
      AND {SYSTEM_SCHEMA_FILTER}
    ORDER BY n.nspname, t.relname, con.conname, k.ord
"""

# Tabellen/Views, die eine View benutzt: über die Rewrite-Regel der View
SQL_VIEW_USAGE = f"""
    SELECT DISTINCT n.nspname  AS schema_name,
           v.relname           AS view_name,
           rn.nspname          AS referenced_schema,
           t.relname           AS referenced_table
    FROM pg_rewrite r
    JOIN pg_class v      ON v.oid = r.ev_class
    JOIN pg_namespace n  ON n.oid = v.relnamespace
    JOIN pg_depend d     ON d.objid = r.oid
                        AND d.classid = 'pg_rewrite'::regclass
                        AND d.refclassid = 'pg_class'::regclass
    JOIN pg_class t      ON t.oid = d.refobjid
    JOIN pg_namespace rn ON rn.oid = t.relnamespace
    WHERE v.relkind = 'v'
      AND t.oid <> v.oid
      AND t.relkind IN ('r', 'p', 'v')
      AND {SYSTEM_SCHEMA_FILTER}
    ORDER BY 1, 2, 3, 4
"""

# Kürzel aus pg_constraint → SQL-Schreibweise
FK_RULES = {"a": "NO ACTION", "r": "RESTRICT", "c": "CASCADE", "n": "SET NULL", "d": "SET DEFAULT"}


def _text(value: Any) -> str | None:
    """Leere Texte werden zu None (z. B. fehlende Kommentare)."""
    if value is None:
        return None
    value = str(value)
    return value or None


def clean_view_definition(text: Any) -> str | None:
    """`pg_get_viewdef` liefert ` SELECT …;` – ohne Rand und Semikolon."""
    value = (_text(text) or "").strip()
    return value.removesuffix(";").rstrip() or None


def _index_kind(is_primary: bool, is_unique: bool) -> IndexKind:
    if is_primary:
        return IndexKind.PRIMARY
    return IndexKind.UNIQUE if is_unique else IndexKind.INDEX


def _is_auto_increment(identity: Any, default: str | None) -> bool:
    """IDENTITY-Spalte oder klassisches serial (Default nextval(...))."""
    return _text(identity) in ("a", "d") or (default or "").startswith("nextval(")


def build_database(
    name: str,
    *,
    schemas: Iterable[Row],
    tables: Iterable[Row],
    columns: Iterable[Row],
    indexes: Iterable[Row],
    foreign_keys: Iterable[Row],
    view_usage: Iterable[Row] = (),
    charset: str | None = None,
    collation: str | None = None,
    server_version: str | None = None,
    connection_profile: str | None = None,
) -> Database:
    """Neutrales Modell aus den Ergebniszeilen der `SQL_*`-Abfragen bauen.

    Schemas ohne Tabellen werden weggelassen. Indexteile, die Ausdrücke
    sind, werden ausgelassen; besteht ein Index nur aus Ausdrücken,
    entfällt er.
    """
    by_schema: dict[str, dict[str, Table]] = {_text(r["schema_name"]): {} for r in schemas}
    for row in tables:
        schema = _text(row["schema_name"])
        t = Table(row["table_name"], comment=_text(row["comment"]))
        if _text(row.get("kind")) == "v":
            t.kind = TableKind.VIEW
            t.definition = clean_view_definition(row.get("definition"))
        by_schema.setdefault(schema, {})[row["table_name"]] = t

    def table(row: Row) -> Table | None:
        return by_schema.get(_text(row["schema_name"]), {}).get(row["table_name"])

    for row in columns:
        t = table(row)
        if t is None:
            continue
        default = row["column_default"]
        t.columns.append(
            Column(
                name=row["column_name"],
                data_type=row["data_type"],
                nullable=bool(row["nullable"]),
                default=default,
                comment=_text(row["comment"]),
                auto_increment=_is_auto_increment(row["identity"], default),
            )
        )

    index_parts: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    index_kinds: dict[tuple[str, str, str], IndexKind] = {}
    for row in indexes:
        key = (_text(row["schema_name"]), row["table_name"], row["index_name"])
        if table(row) is None:
            continue
        index_kinds.setdefault(key, _index_kind(row["is_primary"], row["is_unique"]))
        if row["column_name"] is not None:
            index_parts[key].append(row["column_name"])
    for key, kind in index_kinds.items():
        cols = tuple(index_parts.get(key, ()))
        if cols:
            by_schema[key[0]][key[1]].indexes.append(Index(key[2], kind, cols))

    fk_rows: dict[tuple[str, str, str], list[Row]] = defaultdict(list)
    for row in foreign_keys:
        if table(row) is not None:
            fk_rows[(_text(row["schema_name"]), row["table_name"], row["constraint_name"])].append(row)
    for (schema, table_name, fk_name), rows in fk_rows.items():
        first = rows[0]
        by_schema[schema][table_name].foreign_keys.append(
            ForeignKey(
                name=fk_name,
                columns=tuple(r["column_name"] for r in rows),
                referenced_table=TableRef(name, _text(first["referenced_schema"]), first["referenced_table"]),
                referenced_columns=tuple(r["referenced_column"] for r in rows),
                on_update=FK_RULES.get(_text(first["update_rule"]) or ""),
                on_delete=FK_RULES.get(_text(first["delete_rule"]) or ""),
            )
        )

    for row in view_usage:
        view = table({"schema_name": row["schema_name"], "table_name": row["view_name"]})
        if view is not None and view.is_view:
            ref = TableRef(name, _text(row["referenced_schema"]), row["referenced_table"])
            if ref not in view.view_dependencies:
                view.view_dependencies.append(ref)

    return Database(
        name=name,
        dialect=Dialect.POSTGRESQL,
        schemas=[Schema(s, list(t.values())) for s, t in by_schema.items() if t],
        connection_profile=connection_profile,
        server_version=server_version,
        charset=charset,
        collation=collation,
    )


class PostgresAdapter(SchemaAdapter):
    """Liest Datenbanken eines PostgreSQL-Servers."""

    dialect = Dialect.POSTGRESQL
    DEFAULT_PORT = DEFAULT_PORT

    def __init__(self, profile: ConnectionProfile, password: str | None = None) -> None:
        super().__init__(profile, password)
        self._conn = None

    def _open(self, database: str):
        import psycopg
        from psycopg.rows import dict_row

        try:
            return psycopg.connect(
                host=self.profile.host,
                port=self.profile.port or DEFAULT_PORT,
                user=self.profile.user,
                password=self._password or "",
                dbname=database,
                connect_timeout=10,
                row_factory=dict_row,
                autocommit=True,
            )
        except psycopg.Error as exc:
            raise AdapterError(f"Verbindung zu {self.profile.host}/{database} fehlgeschlagen: {exc}") from exc

    def connect(self) -> None:
        self._conn = self._open(self.profile.options.get("database", MAINTENANCE_DATABASE))

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @staticmethod
    def _query(conn, sql: str, *params: Any) -> list[Row]:
        with conn.cursor() as cursor:
            cursor.execute(sql, params or None)
            return list(cursor.fetchall())

    def _require_connection(self):
        if self._conn is None:
            raise AdapterError("Keine Verbindung – zuerst connect() aufrufen")
        return self._conn

    def server_version(self) -> str:
        return str(self._query(self._require_connection(), "SHOW server_version")[0]["server_version"])

    def list_databases(self) -> list[str]:
        return [r["name"] for r in self._query(self._require_connection(), SQL_DATABASES)]

    def read_database(self, name: str) -> Database:
        self._require_connection()
        if name not in self.list_databases():
            raise AdapterError(f"Datenbank {name!r} nicht gefunden")
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
                charset=_text(info["charset"]),
                collation=_text(info["collation"]),
                server_version=self.server_version(),
                connection_profile=self.profile.name,
            )
        except AdapterError:
            raise
        except Exception as exc:  # psycopg.Error, ohne den Import zu erzwingen
            raise AdapterError(f"Fehler beim Lesen von {name!r}: {exc}") from exc
        finally:
            conn.close()
