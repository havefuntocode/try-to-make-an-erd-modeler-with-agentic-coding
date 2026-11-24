"""SQL-Export: `CREATE`-Skripte für MySQL, MariaDB, PostgreSQL und SQL Server.

Sicherheit: Ein Skript legt immer eine **neue** Datenbank an und verändert nie
eine bestehende. Es enthält kein `DROP` und bricht ab, wenn es die Datenbank
schon gibt (Regel in PROJEKT.md, Abschnitt 13):
- MySQL/MariaDB: `CREATE DATABASE` ohne `IF NOT EXISTS` – Fehler, wenn vorhanden.
- SQL Server: Prüfung vorab; ist die Datenbank da, `RAISERROR` und `SET NOEXEC ON`,
  damit auch die folgenden Abschnitte (nach `GO`) nicht ausgeführt werden.
- PostgreSQL: Die Datenbank wird vorher von Hand angelegt und verbunden (eine
  Verbindung gilt für genau eine Datenbank). Das Skript läuft in einer Transaktion
  und bricht ab, wenn die verbundene Datenbank anders heißt oder nicht leer ist.

Datentypen sind nativ gespeichert und werden unverändert übernommen.
Fremdschlüssel kommen nach den Tabellen als `ALTER TABLE … ADD CONSTRAINT`,
damit die Reihenfolge der Tabellen keine Rolle spielt (auch bei Zyklen).
CHECK-Constraints stehen als `CONSTRAINT … CHECK (…)` im `CREATE TABLE`;
ihr Ausdruck wird wie der View-Text unverändert übernommen. Views folgen zum Schluss, sortiert nach ihren Abhängigkeiten; ihr SQL-Text
wird unverändert übernommen (er ist dialektspezifisch).

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
    # Bewusst keine Optionen für DROP oder „in bestehende Datenbank schreiben“
    comments: bool = True  # Tabellen- und Spaltenkommentare


# ---------------------------------------------------------------------- Namen und Werte


def quote(dialect: Dialect, name: str) -> str:
    """Bezeichner in der Schreibweise des Dialekts quoten."""
    if dialect.mysql_family:
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
    builder = {Dialect.MYSQL: _mysql, Dialect.MARIADB: _mysql, Dialect.POSTGRESQL: _postgres,
               Dialect.MSSQL: _mssql}[database.dialect]
    return builder(database, options)


def _tables(database: Database):
    """Nur echte Tabellen; Views liefert `_views`."""
    for schema in database.schemas:
        for table in schema.tables:
            if not table.is_view:
                yield schema.name, table


def _views(database: Database) -> list[tuple[str | None, Table]]:
    """Views so sortiert, dass benutzte Views vorher angelegt werden.

    Zyklen (sollte es nicht geben) brechen ab, statt endlos zu laufen.
    """
    views = {TableRef(database.name, s.name, t.name): (s.name, t)
             for s in database.schemas for t in s.tables if t.is_view}
    ordered: list[tuple[str | None, Table]] = []
    done: set[TableRef] = set()
    visiting: set[TableRef] = set()

    def visit(ref: TableRef) -> None:
        if ref in done or ref in visiting:
            return
        visiting.add(ref)
        for dep in views[ref][1].view_dependencies:
            if dep in views:
                visit(dep)
        visiting.discard(ref)
        done.add(ref)
        ordered.append(views[ref])

    for ref in views:
        visit(ref)
    return ordered


def _view_body(table: Table) -> str | None:
    return (table.definition or "").strip().removesuffix(";").rstrip() or None


def _create_views(database: Database, full, comment) -> list[str]:
    """`CREATE VIEW` je View (Dialekt über `full`/`comment`); Views ohne Text als Hinweis."""
    out: list[str] = []
    for schema, view in _views(database):
        body = _view_body(view)
        if body is None:
            out.append(f"-- View {view.name}: kein SQL-Text vorhanden")
            continue
        out.append(f"CREATE VIEW {full(schema, view.name)} AS\n{body}")
        out += comment(schema, view)
    return out


# ---------------------------------------------------------------------- MySQL (und MariaDB)


def _mysql(db: Database, o: DdlOptions) -> list[str]:
    q = lambda n: quote(Dialect.MYSQL, n)  # noqa: E731
    out: list[str] = []
    charset = f" CHARACTER SET {db.charset}" if db.charset else ""
    collate = f" COLLATE {db.collation}" if db.collation else ""
    # ohne IF NOT EXISTS: gibt es die Datenbank schon, bricht das Skript hier ab
    out.append(f"CREATE DATABASE {q(db.name)}{charset}{collate}")
    out.append(f"USE {q(db.name)}")
    registry = _Names()  # Check-Namen sind in MySQL je Datenbank eindeutig
    hint = db.dialect is Dialect.MYSQL
    for _, table in _tables(db):
        lines = [_mysql_column(c, o) for c in table.columns]
        for index in _ordered_indexes(table):
            lines.append(_mysql_index(index))
        lines += _check_lines(table, Dialect.MYSQL, registry)
        comment = f" COMMENT={literal(table.comment)}" if o.comments and table.comment else ""
        body = ",\n  ".join(lines)
        prefix = ""
        if hint and table.checks:
            prefix = "-- Hinweis: CHECK-Constraints wirken erst ab MySQL 8.0.16 (ältere Versionen ignorieren sie)\n"
            hint = False
        out.append(f"{prefix}CREATE TABLE {q(table.name)} (\n  {body}\n) ENGINE=InnoDB{comment}")
    for _, table in _tables(db):
        for fk in table.foreign_keys:
            target = f"{q(fk.referenced_table.database)}.{q(fk.referenced_table.table)}"
            out.append(f"ALTER TABLE {q(table.name)} ADD CONSTRAINT {q(fk.name)} FOREIGN KEY "
                       f"({_cols(Dialect.MYSQL, fk.columns)}) REFERENCES {target} "
                       f"({_cols(Dialect.MYSQL, fk.referenced_columns)}){_rules(fk)}")
    # MySQL kennt keine Kommentare für Views
    out += _create_views(db, lambda _schema, name: q(name), lambda _schema, _view: [])
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

    # Eine Transaktion: nach einem Fehler (auch aus der Prüfung) wird nichts übernommen,
    # selbst wenn der Client weitermacht (psql ohne ON_ERROR_STOP)
    out.append("BEGIN")
    out.append(_pg_guard(db.name))
    out += [f"CREATE SCHEMA {q(s.name)}" for s in db.schemas if s.name and s.name != "public"]
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
        lines += _check_lines(table, d, registry)
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

    def view_comment(schema, view) -> list[str]:
        if o.comments and view.comment:
            return [f"COMMENT ON VIEW {full(schema, view.name)} IS {literal(view.comment)}"]
        return []

    out += _create_views(db, full, view_comment)
    out.append("COMMIT")
    return out


def _pg_guard(name: str) -> str:
    """Abbruch, wenn die verbundene Datenbank nicht `name` ist oder schon Objekte enthält."""
    d = Dialect.POSTGRESQL
    return (
        "DO $$\n"
        "BEGIN\n"
        f"  IF current_database() <> {literal(name, d)} THEN\n"
        f"    RAISE EXCEPTION 'Skript für Datenbank %, verbunden mit % – abgebrochen', {literal(name, d)}, current_database();\n"
        "  END IF;\n"
        "  IF EXISTS (SELECT 1 FROM pg_catalog.pg_class c\n"
        "             JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace\n"
        "             WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')\n"
        "               AND n.nspname NOT LIKE 'pg\\_%'\n"
        "               AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')) THEN\n"
        f"    RAISE EXCEPTION 'Datenbank % ist nicht leer – abgebrochen', {literal(name, d)};\n"
        "  END IF;\n"
        "END $$"
    )


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

    # Gibt es die Datenbank schon: Fehlermeldung und alle weiteren Abschnitte nur noch
    # übersetzen, nicht ausführen (NOEXEC wirkt über GO hinweg bis SET NOEXEC OFF)
    message = literal(f"Datenbank {db.name} gibt es schon – Skript abgebrochen, nichts geändert.", d)
    out.append(f"IF DB_ID({literal(db.name, d)}) IS NOT NULL\nBEGIN\n"
               f"  RAISERROR({message.replace('%', '%%')}, 16, 1);\n  SET NOEXEC ON;\nEND")
    out.append(f"CREATE DATABASE {q(db.name)}")
    out.append(f"USE {q(db.name)}")
    for s in db.schemas:
        if s.name and s.name != "dbo":
            out.append(f"EXEC({literal('CREATE SCHEMA ' + q(s.name), d)})")
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
        lines += _check_lines(table, d, registry)
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

    def view_comment(schema, view) -> list[str]:
        if o.comments and view.comment:
            return [f"EXEC sp_addextendedproperty N'MS_Description', {literal(view.comment, d)}, "
                    f"N'SCHEMA', {literal(schema or 'dbo', d)}, N'VIEW', {literal(view.name, d)}"]
        return []

    out += _create_views(db, full, view_comment)
    return out


# ---------------------------------------------------------------------- gemeinsam


def _ordered_indexes(table: Table) -> list[Index]:
    return sorted(table.indexes, key=lambda i: i.kind is not IndexKind.PRIMARY)


def _check_lines(table: Table, dialect: Dialect, registry: _Names) -> list[str]:
    """`CONSTRAINT name CHECK (ausdruck)` je Check; leere Ausdrücke entfallen."""
    return [
        f"CONSTRAINT {quote(dialect, registry.take(c.name))} CHECK ({c.expression.strip()})"
        for c in table.checks
        if c.expression.strip()
    ]


def _rules(fk: ForeignKey) -> str:
    """ON DELETE/UPDATE nur, wenn vom Standard (NO ACTION/RESTRICT) abweichend."""
    parts = []
    for label, value in (("DELETE", fk.on_delete), ("UPDATE", fk.on_update)):
        if value and value.upper() not in ("NO ACTION", "RESTRICT"):
            parts.append(f" ON {label} {value.upper()}")
    return "".join(parts)


DIALECT_NAMES = {d: d.label for d in Dialect}

SCRIPT_HINTS = {
    Dialect.MYSQL: "Legt die Datenbank neu an; gibt es sie schon, bricht das Skript beim\n"
                   "-- CREATE DATABASE ab. Nicht mit --force (\"Fehler ignorieren\") ausführen.",
    Dialect.POSTGRESQL: "Läuft in einer Transaktion und bricht ab, wenn die verbundene Datenbank\n"
                        "-- anders heißt oder nicht leer ist – dann wird nichts übernommen.",
    Dialect.MSSQL: "Legt die Datenbank neu an; gibt es sie schon, meldet das Skript einen Fehler\n"
                   "-- und führt nichts weiter aus (SET NOEXEC ON).",
}
SCRIPT_HINTS[Dialect.MARIADB] = SCRIPT_HINTS[Dialect.MYSQL]


def generate_script(diagram: Diagram, databases: list[str] | None = None, options: DdlOptions = DdlOptions()) -> str:
    """Lesbares Skript für die gewählten Datenbanken (Standard: alle)."""
    chosen = [db for db in diagram.databases if databases is None or db.name in databases]
    parts = [f"-- Erzeugt mit dem ER-Diagramm-Editor am {date.today().strftime('%d.%m.%Y')}"]
    for db in chosen:
        tables = sum(1 for _ in _tables(db))
        views = len(_views(db))
        count = f"{tables} Tabellen" + (f", {views} View{'' if views == 1 else 's'}" if views else "")
        parts.append(f"\n-- ===== {db.name} ({DIALECT_NAMES[db.dialect]}, {count}) =====\n")
        parts.append(f"-- {SCRIPT_HINTS[db.dialect]}\n")
        if db.dialect is Dialect.POSTGRESQL:
            parts.append(f"-- Datenbank vorher anlegen und verbinden (z. B. in psql):\n"
                         f"--   CREATE DATABASE {quote(db.dialect, db.name)};\n"
                         f"--   \\connect {db.name}\n")
        separator = "\nGO\n\n" if db.dialect is Dialect.MSSQL else ";\n\n"
        statements = generate_statements(db, options)
        if db.dialect is Dialect.MSSQL:
            statements.append("SET NOEXEC OFF")  # nur im Skript: nächste Datenbank wieder ausführen
        body = separator.join(s for s in statements)
        parts.append(body + (";\n" if db.dialect is not Dialect.MSSQL else "\nGO\n"))
    return "\n".join(parts)


__all__ = ["DdlOptions", "generate_script", "generate_statements", "literal", "quote", "sql_default", "TableRef"]
