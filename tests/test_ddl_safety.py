"""Sicherheitsregel des SQL-Exports: nur neue Datenbanken anlegen, nie eine bestehende ändern.

Die Live-Tests legen eine Wegwerf-Datenbank `erd_test_safety` mit einer Tabelle
`alt` an und führen dann ein Skript für dieselbe Datenbank aus – wie ein Client,
der nach Fehlern weitermacht (SSMS, psql ohne ON_ERROR_STOP) bzw. wie der
mysql-Client, der beim ersten Fehler stoppt. Danach darf sich nichts geändert haben.
"""

import pytest

from erd_editor.export.ddl import DdlOptions, generate_script, generate_statements
from erd_editor.model import Column, Database, Dialect, Diagram, Index, IndexKind, Schema, Table
from erd_editor.model.types import DEFAULT_SCHEMAS, ID_TYPES

SAFETY = "erd_test_safety"


def model(dialect: Dialect, name: str = SAFETY) -> Database:
    neu = Table("neu", [Column("id", ID_TYPES[dialect], False)], [Index("PRIMARY", IndexKind.PRIMARY, ("id",))])
    return Database(name, dialect, [Schema(DEFAULT_SCHEMAS[dialect], [neu])], modeled=True)


# ------------------------------------------------------------------ ohne Server


def test_options_offer_no_drop():
    assert set(DdlOptions.__dataclass_fields__) == {"comments"}


@pytest.mark.parametrize("dialect", list(Dialect))
def test_script_never_drops_or_reuses(dialect):
    script = generate_script(Diagram([model(dialect)])).upper()
    assert "DROP " not in script
    assert "IF NOT EXISTS" not in script  # nichts „falls vorhanden, weiter“


@pytest.mark.parametrize("dialect", [Dialect.MYSQL, Dialect.MARIADB])
def test_mysql_creates_database_first(dialect):
    stmts = generate_statements(model(dialect))
    assert stmts[0] == f"CREATE DATABASE `{SAFETY}`" and stmts[1] == f"USE `{SAFETY}`"


def test_mssql_guard_comes_first():
    stmts = generate_statements(model(Dialect.MSSQL))
    assert stmts[0].startswith(f"IF DB_ID(N'{SAFETY}') IS NOT NULL") and "SET NOEXEC ON" in stmts[0]
    assert stmts[1] == f"CREATE DATABASE [{SAFETY}]"
    script = generate_script(Diagram([model(Dialect.MSSQL)]))
    assert script.rstrip().endswith("SET NOEXEC OFF\nGO")


def test_mssql_guard_escapes_percent():
    stmt = generate_statements(model(Dialect.MSSQL, "100%"))[0]
    assert "Datenbank 100%% gibt es schon" in stmt  # RAISERROR: % ist Formatzeichen


def test_postgres_runs_in_one_guarded_transaction():
    stmts = generate_statements(model(Dialect.POSTGRESQL))
    assert stmts[0] == "BEGIN" and stmts[1].startswith("DO $$") and stmts[-1] == "COMMIT"
    assert f"current_database() <> '{SAFETY}'" in stmts[1] and "nicht leer" in stmts[1]


def test_script_explains_safety():
    script = generate_script(Diagram([model(d, f"db_{d.value}") for d in Dialect]))
    assert script.count("bricht") >= 3 and "--force" in script and "NOEXEC" in script


# ------------------------------------------------------------------ gegen echte Server


def _keyring(account):
    try:
        import keyring

        return keyring.get_password("erd-editor-test", account)
    except Exception:
        return None


def _run(execute, statements, keep_going: bool) -> list[str]:
    """Anweisungen ausführen; Fehler sammeln (weitermachen) oder beim ersten stoppen."""
    errors = []
    for statement in statements:
        if statement.startswith("--"):
            continue
        try:
            execute(statement)
        except Exception as exc:  # noqa: BLE001 – Treiberfehler aller Dialekte
            errors.append(str(exc))
            if not keep_going:
                break
    return errors


@pytest.mark.parametrize(("account", "port", "dialect"),
                         [("root", 3306, Dialect.MYSQL), ("root@mariadb", 3307, Dialect.MARIADB)],
                         ids=["mysql", "mariadb"])
def test_live_mysql_family_leaves_existing_database_alone(account, port, dialect):
    import pymysql

    pw = _keyring(account)
    if pw is None:
        pytest.skip(f"kein Passwort für {account}")
    try:
        conn = pymysql.connect(host="localhost", port=port, user="root", password=pw, autocommit=True)
    except pymysql.MySQLError as exc:
        pytest.skip(f"Server nicht erreichbar: {exc}")
    cur = conn.cursor()
    try:
        cur.execute(f"DROP DATABASE IF EXISTS {SAFETY}")
        cur.execute(f"CREATE DATABASE {SAFETY}")
        cur.execute(f"CREATE TABLE {SAFETY}.alt (id int PRIMARY KEY)")
        cur.execute(f"INSERT INTO {SAFETY}.alt VALUES (1)")
        errors = _run(cur.execute, generate_statements(model(dialect)), keep_going=False)
        assert errors and "exists" in errors[0]
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (SAFETY,))
        assert [r[0] for r in cur.fetchall()] == ["alt"]
        cur.execute(f"SELECT COUNT(*) FROM {SAFETY}.alt")
        assert cur.fetchone()[0] == 1
    finally:
        cur.execute(f"DROP DATABASE IF EXISTS {SAFETY}")
        conn.close()


def test_live_mssql_leaves_existing_database_alone():
    pyodbc = pytest.importorskip("pyodbc")
    from erd_editor.adapters.base import AdapterError
    from erd_editor.adapters.mssql import MssqlAdapter, connection_string
    from erd_editor.model import ConnectionProfile

    profile = ConnectionProfile("t", Dialect.MSSQL, "localhost", None, None, {"auth": "windows"})
    try:
        with MssqlAdapter(profile) as a:
            driver = a.driver()
    except AdapterError as exc:
        pytest.skip(f"SQL Server nicht erreichbar: {exc}")
    admin = pyodbc.connect(connection_string(profile, None, "master", driver), autocommit=True)
    drop = (f"IF DB_ID('{SAFETY}') IS NOT NULL BEGIN ALTER DATABASE {SAFETY} SET SINGLE_USER WITH ROLLBACK "
            f"IMMEDIATE; DROP DATABASE {SAFETY}; END")
    try:
        admin.execute(drop)
        admin.execute(f"CREATE DATABASE {SAFETY}")
        admin.execute(f"CREATE TABLE {SAFETY}.dbo.alt (id int PRIMARY KEY)")
        # Eigene Sitzung wie in SSMS: nach dem Fehler geht es mit dem nächsten Abschnitt weiter
        session = pyodbc.connect(connection_string(profile, None, "master", driver), autocommit=True)
        try:
            errors = _run(session.execute, generate_statements(model(Dialect.MSSQL)), keep_going=True)
        finally:
            session.close()
        assert errors and "gibt es schon" in errors[0]
        names = [r[0] for r in admin.execute(f"SELECT name FROM {SAFETY}.sys.tables").fetchall()]
        assert names == ["alt"]
    finally:
        admin.execute(drop)
        admin.close()


def _pg_connect(dbname, pw):
    import psycopg

    return psycopg.connect(host="localhost", user="postgres", password=pw, dbname=dbname, autocommit=True)


@pytest.mark.parametrize("script_for", [SAFETY, "erd_test_anders"], ids=["nicht-leer", "falsche-datenbank"])
def test_live_postgres_leaves_existing_database_alone(script_for):
    psycopg = pytest.importorskip("psycopg")
    pw = _keyring("postgres")
    if pw is None:
        pytest.skip("kein PostgreSQL-Passwort")
    try:
        admin = _pg_connect("postgres", pw)
    except psycopg.Error as exc:
        pytest.skip(f"PostgreSQL nicht erreichbar: {exc}")
    try:
        admin.execute(f"DROP DATABASE IF EXISTS {SAFETY} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {SAFETY}")
        with _pg_connect(SAFETY, pw) as conn:
            conn.execute("CREATE TABLE alt (id int PRIMARY KEY)")
            # psql ohne ON_ERROR_STOP macht nach Fehlern weiter
            errors = _run(conn.execute, generate_statements(model(Dialect.POSTGRESQL, script_for)), keep_going=True)
            assert errors and "abgebrochen" in errors[0]
            names = [r[0] for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'").fetchall()]
            assert names == ["alt"]
    finally:
        admin.execute(f"DROP DATABASE IF EXISTS {SAFETY} WITH (FORCE)")
        admin.close()


def test_live_postgres_empty_database_works():
    """Gegenprobe: in einer leeren, richtig benannten Datenbank läuft das Skript durch."""
    psycopg = pytest.importorskip("psycopg")
    pw = _keyring("postgres")
    if pw is None:
        pytest.skip("kein PostgreSQL-Passwort")
    try:
        admin = _pg_connect("postgres", pw)
    except psycopg.Error as exc:
        pytest.skip(f"PostgreSQL nicht erreichbar: {exc}")
    try:
        admin.execute(f"DROP DATABASE IF EXISTS {SAFETY} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {SAFETY}")
        with _pg_connect(SAFETY, pw) as conn:
            assert _run(conn.execute, generate_statements(model(Dialect.POSTGRESQL)), keep_going=False) == []
            names = [r[0] for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'").fetchall()]
            assert names == ["neu"]
    finally:
        admin.execute(f"DROP DATABASE IF EXISTS {SAFETY} WITH (FORCE)")
        admin.close()
