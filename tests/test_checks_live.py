"""CHECK-Constraints gegen echte Server: einlesen und Roundtrip über den SQL-Export.

Legt je Server die Datenbanken `erd_test_checks` (Quelle, per Hand-SQL) und
`erd_test_checks_copy` (aus dem SQL-Export) an und löscht beide am Ende.
Ohne Passwort im keyring bzw. ohne erreichbaren Server wird übersprungen.
"""

from __future__ import annotations

import pytest

from erd_editor.export.ddl import generate_statements
from erd_editor.model import ConnectionProfile, Database, Dialect
from tests.test_ddl import keyring_password, renamed

SOURCE = "erd_test_checks"
COPY = "erd_test_checks_copy"

MYSQL_TABLE = """
CREATE TABLE artikel (
    id     INT NOT NULL PRIMARY KEY,
    preis  DECIMAL(10,2) NOT NULL CHECK (preis >= 0),
    rabatt DECIMAL(10,2),
    status VARCHAR(10) NOT NULL,
    CONSTRAINT ck_status CHECK (status IN ('offen', 'erledigt')),
    CONSTRAINT ck_rabatt CHECK (rabatt <= preis)
)"""

POSTGRES_TABLE = """
CREATE TABLE artikel (
    id     integer NOT NULL PRIMARY KEY,
    preis  numeric(10,2) NOT NULL CHECK (preis >= 0),
    rabatt numeric(10,2),
    status varchar(10) NOT NULL,
    CONSTRAINT ck_status CHECK (status IN ('offen', 'erledigt')),
    CONSTRAINT ck_rabatt CHECK (rabatt <= preis)
)"""

MSSQL_TABLE = """
CREATE TABLE dbo.artikel (
    id     int NOT NULL PRIMARY KEY,
    preis  decimal(10,2) NOT NULL CHECK (preis >= 0),
    rabatt decimal(10,2),
    status nvarchar(10) NOT NULL,
    CONSTRAINT ck_status CHECK (status IN (N'offen', N'erledigt')),
    CONSTRAINT ck_rabatt CHECK (rabatt <= preis)
)"""


def _mysql_family(account: str, port: int, dialect: Dialect, extra: list[str]) -> tuple[Database, Database]:
    import pymysql

    from erd_editor.adapters.base import AdapterError
    from erd_editor.adapters.mysql import MySqlAdapter

    pw = keyring_password(account)
    if pw is None:
        pytest.skip(f"kein Passwort für {account}")
    try:
        conn = pymysql.connect(host="localhost", port=port, user="root", password=pw, autocommit=True)
    except pymysql.MySQLError as exc:
        pytest.skip(f"Server nicht erreichbar: {exc}")
    profile = ConnectionProfile("t", dialect, "localhost", port, "root")
    try:
        with conn.cursor() as cur:
            for db in (SOURCE, COPY):
                cur.execute(f"DROP DATABASE IF EXISTS {db}")
            cur.execute(f"CREATE DATABASE {SOURCE}")
            cur.execute(f"USE {SOURCE}")
            cur.execute(MYSQL_TABLE)
            for statement in extra:
                cur.execute(statement)
        try:
            with MySqlAdapter(profile, pw) as a:
                original = a.read_database(SOURCE)
        except AdapterError as exc:
            pytest.skip(f"Server nicht erreichbar: {exc}")
        with conn.cursor() as cur:
            for statement in generate_statements(renamed(original, COPY)):
                cur.execute(statement)
        with MySqlAdapter(profile, pw) as a:
            copy_db = a.read_database(COPY)
    finally:
        with conn.cursor() as cur:
            for db in (SOURCE, COPY):
                cur.execute(f"DROP DATABASE IF EXISTS {db}")
        conn.close()
    return original, copy_db


def _mysql():
    return _mysql_family("root", 3306, Dialect.MYSQL, [])


def _mariadb():
    # json legt in MariaDB selbst einen Check json_valid(...) an
    return _mysql_family("root@mariadb", 3307, Dialect.MARIADB,
                         ["CREATE TABLE dokument (id INT NOT NULL PRIMARY KEY, inhalt JSON)"])


def _postgres():
    import psycopg

    from erd_editor.adapters.postgres import PostgresAdapter

    pw = keyring_password("postgres")
    if pw is None:
        pytest.skip("kein PostgreSQL-Passwort")
    try:
        admin = psycopg.connect(host="localhost", user="postgres", password=pw, dbname="postgres", autocommit=True)
    except psycopg.Error as exc:
        pytest.skip(f"Server nicht erreichbar: {exc}")
    profile = ConnectionProfile("t", Dialect.POSTGRESQL, "localhost", None, "postgres")
    try:
        for db in (SOURCE, COPY):
            admin.execute(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)")
            admin.execute(f"CREATE DATABASE {db} ENCODING 'UTF8' TEMPLATE template0")
        with psycopg.connect(host="localhost", user="postgres", password=pw, dbname=SOURCE, autocommit=True) as conn:
            conn.execute(POSTGRES_TABLE)
            # nachträglich, ohne Prüfung vorhandener Zeilen
            conn.execute("ALTER TABLE artikel ADD CONSTRAINT ck_id CHECK (id > 0) NOT VALID")
        with PostgresAdapter(profile, pw) as a:
            original = a.read_database(SOURCE)
        with psycopg.connect(host="localhost", user="postgres", password=pw, dbname=COPY, autocommit=True) as conn:
            for statement in generate_statements(renamed(original, COPY)):
                if not statement.startswith("--"):
                    conn.execute(statement)
        with PostgresAdapter(profile, pw) as a:
            copy_db = a.read_database(COPY)
    finally:
        for db in (SOURCE, COPY):
            admin.execute(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)")
        admin.close()
    return original, copy_db


def _mssql():
    from erd_editor.adapters.base import AdapterError
    from erd_editor.adapters.mssql import MssqlAdapter, connection_string

    import pyodbc

    profile = ConnectionProfile("t", Dialect.MSSQL, "localhost", None, None, {"auth": "windows"})
    try:
        with MssqlAdapter(profile) as a:
            driver = a.driver()
    except AdapterError as exc:
        pytest.skip(f"SQL Server nicht erreichbar: {exc}")
    conn = pyodbc.connect(connection_string(profile, None, "master", driver), autocommit=True)

    def drop(db: str) -> str:
        return (f"IF DB_ID('{db}') IS NOT NULL BEGIN ALTER DATABASE {db} SET SINGLE_USER WITH ROLLBACK IMMEDIATE; "
                f"DROP DATABASE {db}; END")

    try:
        cur = conn.cursor()
        for db in (SOURCE, COPY):
            cur.execute(drop(db))
        cur.execute(f"CREATE DATABASE {SOURCE}")
        cur.execute(f"USE {SOURCE}")
        cur.execute(MSSQL_TABLE)
        cur.execute("USE master")
        with MssqlAdapter(profile) as a:
            original = a.read_database(SOURCE)
        for statement in generate_statements(renamed(original, COPY)):
            if not statement.startswith("--"):
                cur.execute(statement)
        cur.execute("USE master")
        with MssqlAdapter(profile) as a:
            copy_db = a.read_database(COPY)
    finally:
        cur = conn.cursor()
        cur.execute("USE master")
        for db in (SOURCE, COPY):
            cur.execute(drop(db))
        conn.close()
    return original, copy_db


def checks_of(db: Database) -> dict[str, list[tuple[str, str, tuple[str, ...]]]]:
    return {t.name: sorted((c.name, c.expression, c.columns) for c in t.checks)
            for s in db.schemas for t in s.tables}


@pytest.mark.parametrize("roundtrip", [_mysql, _mariadb, _postgres, _mssql],
                         ids=["mysql", "mariadb", "postgresql", "mssql"])
def test_checks_read_and_roundtrip(roundtrip):
    original, copy_db = roundtrip()
    found = checks_of(original)
    by_columns = {c[2]: c for c in found["artikel"]}
    # Spalten- und Tabellen-Checks werden erkannt, Spalten aus dem Ausdruck bestimmt
    assert set(by_columns) >= {("preis",), ("status",), ("rabatt", "preis")}
    assert by_columns[("rabatt", "preis")][0] == "ck_rabatt"
    assert by_columns[("status",)][0] == "ck_status"
    assert not by_columns[("preis",)][1].upper().startswith("CHECK")
    if original.dialect is Dialect.MARIADB:
        assert [c[2] for c in found["dokument"]] == [("inhalt",)]
        assert "json_valid" in found["dokument"][0][1]
    if original.dialect is Dialect.POSTGRESQL:
        assert by_columns[("id",)][1] == "id > 0"  # NOT VALID ist abgeschnitten
    # Roundtrip: gleiche Namen, Spalten und Ausdrücke. Ausnahme PostgreSQL: IN (…) speichert
    # der Server als = ANY (ARRAY[…]) und schreibt das beim erneuten Einlesen gleichbedeutend um
    copied = checks_of(copy_db)
    if original.dialect is Dialect.POSTGRESQL:
        strip = lambda d: {t: [(n, cols) for n, _, cols in cs] for t, cs in d.items()}  # noqa: E731
        assert strip(copied) == strip(found)
        found = {t: [c for c in cs if c[0] != "ck_status"] for t, cs in found.items()}
        copied = {t: [c for c in cs if c[0] != "ck_status"] for t, cs in copied.items()}
    assert copied == found
