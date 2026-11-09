"""SQL-Export: `CREATE`-Skripte für MySQL, PostgreSQL und SQL Server.

Datentypen sind nativ gespeichert und werden unverändert übernommen.
Fremdschlüssel kommen am Ende als `ALTER TABLE … ADD CONSTRAINT`, damit
die Reihenfolge der Tabellen keine Rolle spielt (auch bei Zyklen).

`generate_statements()` liefert einzelne Anweisungen (zum Ausführen),
`generate_script()` ein lesbares Skript mit Kopf und Trennern.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from erd_editor.model import Column, Database, Diagram, Dialect, ForeignKey, Index, IndexKind, Table, TableRef


@dataclass(frozen=True)
class DdlOptions:
    create_database: bool = True  # CREATE DATABASE / CREATE SCHEMA
    drop_existing: bool = False  # vorhandene Tabellen vorher löschen
    comments: bool = True  # Tabellen- und Spaltenkommentare


# ---------------------------------------------------------------------- Namen und Werte


def quote(dialect: Dialect, name: str) -> str:
    """Bezeichner in der Schreibweise des Dialekts quoten."""
    if dialect is Dialect.MYSQL:
        return "`" + name.replace("`", "``") + "`"
    if dialect is Dialect.MSSQL:
        return "[" + name.replace("]", "]]") + "]"
    return '"' + name.replace('"', '""') + '"'


def literal(text: str, dialect: Dialect = Dialect.MYSQL) -> str:
    """Zeichenkette als SQL-Literal (bei SQL Server als Unicode N'…')."""
    body = "'" + text.replace("'", "''") + "'"
    return "N" + body if dialect is Dialect.MSSQL else body


_KEYWORDS = {
    "NULL", "TRUE", "FALSE", "CURRENT_TIMESTAMP", "CURRENT_DATE", "CURRENT_TIME",
    "LOCALTIME", "LOCALTIMESTAMP", "CURRENT_USER", "SESSION_USER",
}
_NUMBER = re.compile(r"^[+-]?\d+(\.\d+)?$")


def sql_default(value: str, dialect: Dialect) -> str:
    """Default-Wert als SQL-Ausdruck.

    Was schon ein Ausdruck ist (Zahl, Schlüsselwort, Funktionsaufruf,
    Literal, PostgreSQL-Cast), bleibt unverändert; alles andere – etwa ein
    von MySQL gelieferter Text ohne Anführungszeichen – wird als Literal gequotet.
    """
    v = value.strip()
    if v == "":
        return "''"
    if _NUMBER.match(v) or v.upper() in _KEYWORDS or v.startswith(("'", "N'", "(")) or "(" in v or "::" in v:
        return v
    return literal(v, dialect)


def _cols(dialect: Dialect, names) -> str:
    return ", ".join(quote(dialect, n) for n in names)


class _Names:
    """Eindeutige Constraint-/Indexnamen je Schema (PostgreSQL, SQL Server)."""

    def __init__(self) -> None:
        self.used: set[str] = set()

    def take(self, name: str) -> str:
        candidate, n = name, 2
        while candidate.lower() in self.used:
            candidate = f"{name}_{n}"
            n += 1
        self.used.add(candidate.lower())
        return candidate


# ---------------------------------------------------------------------- Anweisungen


def generate_statements(database: Database, options: DdlOptions = DdlOptions()) -> list[str]:
    """Alle Anweisungen für eine Datenbank (ohne Trenner)."""
    builder = {Dialect.MYSQL: _mysql, Dialect.POSTGRESQL: _postgres, Dialect.MSSQL: _mssql}[database.dialect]
    return builder(database, options)


def _tables(database: Database):
    for schema in database.schemas:
        for table in schema.tables:
            yield schema.name, table


# ---------------------------------------------------------------------- MySQL


def _mysql(db: Database, o: DdlOptions) -> list[str]:
    q = lambda n: quote(Dialect.MYSQL, n)  # noqa: E731
    out: list[str] = []
    if o.create_database:
        charset = f" CHARACTER SET {db.charset}" if db.charset else ""
        collate = f" COLLATE {db.collation}" if db.collation else ""
        out.append(f"CREATE DATABASE IF NOT EXISTS {q(db.name)}{charset}{collate}")
    out.append(f"USE {q(db.name)}")
    if o.drop_existing:
        out.append("SET FOREIGN_KEY_CHECKS = 0")
        out += [f"DROP TABLE IF EXISTS {q(t.name)}" for _, t in _tables(db)]
        out.append("SET FOREIGN_KEY_CHECKS = 1")
    for _, table in _tables(db):
        lines = [_mysql_column(c, o) for c in table.columns]
        for index in _ordered_indexes(table):
            lines.append(_mysql_index(index))
        comment = f" COMMENT={literal(table.comment)}" if o.comments and table.comment else ""
        body = ",\n  ".join(lines)
        out.append(f"CREATE TABLE {q(table.name)} (\n  {body}\n) ENGINE=InnoDB{comment}")
    for _, table in _tables(db):
        for fk in table.foreign_keys:
            target = f"{q(fk.referenced_table.database)}.{q(fk.referenced_table.table)}"
            out.append(f"ALTER TABLE {q(table.name)} ADD CONSTRAINT {q(fk.name)} FOREIGN KEY "
                       f"({_cols(Dialect.MYSQL, fk.columns)}) REFERENCES {target} "
                       f"({_cols(Dialect.MYSQL, fk.referenced_columns)}){_rules(fk)}")
    return out


def _mysql_column(c: Column, o: DdlOptions) -> str:
    parts = [quote(Dialect.MYSQL, c.name), c.data_type, "NULL" if c.nullable else "NOT NULL"]
    if c.default is not None and not c.auto_increment:
        parts.append(f"DEFAULT {sql_default(c.default, Dialect.MYSQL)}")
    if c.auto_increment:
        parts.append("AUTO_INCREMENT")
    if o.comments and c.comment:
        parts.append(f"COMMENT {literal(c.comment)}")
    return " ".join(parts)


def _mysql_index(index: Index) -> str:
    cols = _cols(Dialect.MYSQL, index.columns)
    name = quote(Dialect.MYSQL, index.name)
    return {
        IndexKind.PRIMARY: f"PRIMARY KEY ({cols})",
        IndexKind.UNIQUE: f"UNIQUE KEY {name} ({cols})",
        IndexKind.FULLTEXT: f"FULLTEXT KEY {name} ({cols})",
        IndexKind.SPATIAL: f"SPATIAL KEY {name} ({cols})",
    }.get(index.kind, f"KEY {name} ({cols})")


# ---------------------------------------------------------------------- PostgreSQL


def _postgres(db: Database, o: DdlOptions) -> list[str]:
    d = Dialect.POSTGRESQL
    q = lambda n: quote(d, n)  # noqa: E731
    out: list[str] = []
    names: dict[str | None, _Names] = {}

    def full(schema: str | None, table: str) -> str:
        return f"{q(schema)}.{q(table)}" if schema else q(table)

    if o.create_database:
        out += [f"CREATE SCHEMA IF NOT EXISTS {q(s.name)}" for s in db.schemas if s.name and s.name != "public"]
    if o.drop_existing:
        out += [f"DROP TABLE IF EXISTS {full(s, t.name)} CASCADE" for s, t in _tables(db)]
    for schema, table in _tables(db):
        registry = names.setdefault(schema, _Names())
        lines = []
        for c in table.columns:
            parts = [q(c.name), c.data_type]
            if c.auto_increment:
                parts.append("GENERATED BY DEFAULT AS IDENTITY")
            parts.append("NULL" if c.nullable else "NOT NULL")
            if c.default is not None and not c.auto_increment:
                parts.append(f"DEFAULT {sql_default(c.default, d)}")
            lines.append(" ".join(parts))
        later = []
        for index in _ordered_indexes(table):
            cols = _cols(d, index.columns)
            if index.kind is IndexKind.PRIMARY:
                lines.append(f"CONSTRAINT {q(registry.take(_pg_name(index.name, table.name, 'pkey')))} PRIMARY KEY ({cols})")
            elif index.kind is IndexKind.UNIQUE:
                lines.append(f"CONSTRAINT {q(registry.take(index.name))} UNIQUE ({cols})")
            elif index.kind is IndexKind.INDEX:
                later.append(f"CREATE INDEX {q(registry.take(index.name))} ON {full(schema, table.name)} ({cols})")
            else:
                later.append(f"-- Index {index.name} ({index.kind.value}) hat in PostgreSQL keine direkte Entsprechung")
        body = ",\n  ".join(lines)
        out.append(f"CREATE TABLE {full(schema, table.name)} (\n  {body}\n)")
        out += later
        if o.comments:
            if table.comment:
                out.append(f"COMMENT ON TABLE {full(schema, table.name)} IS {literal(table.comment)}")
            for c in table.columns:
                if c.comment:
                    out.append(f"COMMENT ON COLUMN {full(schema, table.name)}.{q(c.name)} IS {literal(c.comment)}")
    for schema, table in _tables(db):
        registry = names[schema]
        for fk in table.foreign_keys:
            target = full(fk.referenced_table.schema, fk.referenced_table.table)
            out.append(f"ALTER TABLE {full(schema, table.name)} ADD CONSTRAINT {q(registry.take(fk.name))} "
                       f"FOREIGN KEY ({_cols(d, fk.columns)}) REFERENCES {target} "
                       f"({_cols(d, fk.referenced_columns)}){_rules(fk)}")
    return out


def _pg_name(name: str, table: str, suffix: str) -> str:
    """MySQL nennt jeden Primärschlüssel `PRIMARY` – in PostgreSQL muss der Name eindeutig sein."""
    return f"{table}_{suffix}" if name.upper() == "PRIMARY" else name


# ---------------------------------------------------------------------- SQL Server


def _mssql(db: Database, o: DdlOptions) -> list[str]:
    d = Dialect.MSSQL
    q = lambda n: quote(d, n)  # noqa: E731
    out: list[str] = []
    names: dict[str | None, _Names] = {}

    def full(schema: str | None, table: str) -> str:
        return f"{q(schema or 'dbo')}.{q(table)}"

    if o.create_database:
        out.append(f"IF DB_ID({literal(db.name, d)}) IS NULL CREATE DATABASE {q(db.name)}")
    out.append(f"USE {q(db.name)}")
    if o.create_database:
        for s in db.schemas:
            if s.name and s.name != "dbo":
                out.append(f"IF SCHEMA_ID({literal(s.name, d)}) IS NULL EXEC({literal('CREATE SCHEMA ' + q(s.name), d)})")
    if o.drop_existing:
        for s, t in _tables(db):
            for fk in t.foreign_keys:
                object_name = literal(q(s or "dbo") + "." + q(fk.name), d)
                out.append(f"IF OBJECT_ID({object_name}, N'F') IS NOT NULL "
                           f"ALTER TABLE {full(s, t.name)} DROP CONSTRAINT {q(fk.name)}")
        out += [f"DROP TABLE IF EXISTS {full(s, t.name)}" for s, t in _tables(db)]
    for schema, table in _tables(db):
        registry = names.setdefault(schema, _Names())
        lines = []
        for c in table.columns:
            parts = [q(c.name), c.data_type]
            if c.auto_increment:
                parts.append("IDENTITY(1,1)")
            parts.append("NULL" if c.nullable else "NOT NULL")
            if c.default is not None and not c.auto_increment:
                df = registry.take(f"df_{table.name}_{c.name}")
                parts.append(f"CONSTRAINT {q(df)} DEFAULT ({sql_default(c.default, d)})")
            lines.append(" ".join(parts))
        later = []
        for index in _ordered_indexes(table):
            cols = _cols(d, index.columns)
            if index.kind is IndexKind.PRIMARY:
                lines.append(f"CONSTRAINT {q(registry.take(_pg_name(index.name, table.name, 'pkey')))} PRIMARY KEY ({cols})")
            elif index.kind is IndexKind.UNIQUE:
                lines.append(f"CONSTRAINT {q(registry.take(index.name))} UNIQUE ({cols})")
            elif index.kind is IndexKind.INDEX:
                later.append(f"CREATE INDEX {q(index.name)} ON {full(schema, table.name)} ({cols})")
            else:
                later.append(f"-- Index {index.name} ({index.kind.value}) hat in SQL Server keine direkte Entsprechung")
        body = ",\n  ".join(lines)
        out.append(f"CREATE TABLE {full(schema, table.name)} (\n  {body}\n)")
        out += later
        if o.comments:
            s = schema or "dbo"
            if table.comment:
                out.append(f"EXEC sp_addextendedproperty N'MS_Description', {literal(table.comment, d)}, "
                           f"N'SCHEMA', {literal(s, d)}, N'TABLE', {literal(table.name, d)}")
            for c in table.columns:
                if c.comment:
                    out.append(f"EXEC sp_addextendedproperty N'MS_Description', {literal(c.comment, d)}, "
                               f"N'SCHEMA', {literal(s, d)}, N'TABLE', {literal(table.name, d)}, "
                               f"N'COLUMN', {literal(c.name, d)}")
    for schema, table in _tables(db):
        registry = names[schema]
        for fk in table.foreign_keys:
            target = full(fk.referenced_table.schema, fk.referenced_table.table)
            out.append(f"ALTER TABLE {full(schema, table.name)} ADD CONSTRAINT {q(registry.take(fk.name))} "
                       f"FOREIGN KEY ({_cols(d, fk.columns)}) REFERENCES {target} "
                       f"({_cols(d, fk.referenced_columns)}){_rules(fk)}")
    return out


# ---------------------------------------------------------------------- gemeinsam


def _ordered_indexes(table: Table) -> list[Index]:
    return sorted(table.indexes, key=lambda i: i.kind is not IndexKind.PRIMARY)


def _rules(fk: ForeignKey) -> str:
    """ON DELETE/UPDATE nur, wenn vom Standard (NO ACTION/RESTRICT) abweichend."""
    parts = []
    for label, value in (("DELETE", fk.on_delete), ("UPDATE", fk.on_update)):
        if value and value.upper() not in ("NO ACTION", "RESTRICT"):
            parts.append(f" ON {label} {value.upper()}")
    return "".join(parts)


DIALECT_NAMES = {Dialect.MYSQL: "MySQL", Dialect.POSTGRESQL: "PostgreSQL", Dialect.MSSQL: "SQL Server"}


def generate_script(diagram: Diagram, databases: list[str] | None = None, options: DdlOptions = DdlOptions()) -> str:
    """Lesbares Skript für die gewählten Datenbanken (Standard: alle)."""
    chosen = [db for db in diagram.databases if databases is None or db.name in databases]
    parts = [f"-- Erzeugt mit dem ER-Diagramm-Editor am {date.today().strftime('%d.%m.%Y')}"]
    for db in chosen:
        tables = sum(len(s.tables) for s in db.schemas)
        parts.append(f"\n-- ===== {db.name} ({DIALECT_NAMES[db.dialect]}, {tables} Tabellen) =====\n")
        if db.dialect is Dialect.POSTGRESQL and options.create_database:
            parts.append(f"-- Datenbank vorher anlegen und verbinden:\n--   CREATE DATABASE {quote(db.dialect, db.name)};\n"
                         f"--   \\connect {db.name}\n")
        separator = "\nGO\n\n" if db.dialect is Dialect.MSSQL else ";\n\n"
        statements = generate_statements(db, options)
        body = separator.join(s for s in statements)
        parts.append(body + (";\n" if db.dialect is not Dialect.MSSQL else "\nGO\n"))
    return "\n".join(parts)


__all__ = ["DdlOptions", "generate_script", "generate_statements", "literal", "quote", "sql_default", "TableRef"]
