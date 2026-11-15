"""Live-Test gegen einen lokalen PostgreSQL-Server mit eingespieltem Testschema.

Voraussetzungen:
- tests/schema/postgres.sql ist eingespielt (Datenbank erd_test_shop)
- Passwort im keyring:  .venv/Scripts/python -m keyring set erd-editor-test postgres
  oder in ERD_PG_PASSWORD
- optional: ERD_PG_USER (postgres), ERD_PG_HOST (localhost), ERD_PG_PORT (5432)

Ohne Passwort wird der Test übersprungen.
"""

import os

import keyring
import pytest

from erd_editor.adapters.base import AdapterError
from erd_editor.adapters.postgres import PostgresAdapter
from erd_editor.analysis import derive_relationships
from erd_editor.model import ConnectionProfile, Database, Diagram, Dialect, Schema, TableRef

DB = "erd_test_shop"
USER = os.environ.get("ERD_PG_USER", "postgres")


def _password():
    if "ERD_PG_PASSWORD" in os.environ:
        return os.environ["ERD_PG_PASSWORD"]
    try:
        return keyring.get_password("erd-editor-test", USER)
    except keyring.errors.KeyringError:
        return None


PASSWORD = _password()
pytestmark = pytest.mark.skipif(PASSWORD is None, reason=f"kein PostgreSQL-Passwort (keyring erd-editor-test/{USER})")


@pytest.fixture(scope="module")
def live() -> Diagram:
    profile = ConnectionProfile(
        "pg-test", Dialect.POSTGRESQL,
        os.environ.get("ERD_PG_HOST", "localhost"), int(os.environ.get("ERD_PG_PORT", "5432")), USER,
    )
    with PostgresAdapter(profile, PASSWORD) as adapter:
        if DB not in adapter.list_databases():
            pytest.skip("Testschema nicht eingespielt (tests/schema/postgres.sql)")
        return Diagram([adapter.read_database(DB)])


@pytest.fixture(scope="module")
def expected(schema_builder) -> Diagram:
    """MySQL-Testschema umgebaut: zwei Datenbanken → zwei Schemas einer Datenbank."""
    mysql = schema_builder("shop", "stammdaten")
    schemas = []
    for db in mysql.databases:
        tables = db.schemas[0].tables
        for table in tables:
            for fk in table.foreign_keys:
                object.__setattr__(fk, "referenced_table", TableRef(DB, fk.referenced_table.database, fk.referenced_table.table))
        schemas.append(Schema(db.name, tables))
    return Diagram([Database(DB, Dialect.POSTGRESQL, schemas)])


def without_partitioned(diagram):
    """Nur die Tabellen des gemeinsamen Testschemas (ohne Partitionstabelle und Views)."""
    return {ref: t for ref, t in diagram.tables() if ref.table != "protokoll" and not t.is_view}


def test_consistent(live):
    assert live.validate() == []


def test_schemas(live):
    assert [s.name for s in live.database(DB).schemas] == ["shop", "stammdaten"]


def test_same_tables_columns_and_nullability(live, expected):
    def shape(tables):
        return {ref.key: [(c.name, c.nullable) for c in t.columns] for ref, t in tables.items()}

    assert shape(without_partitioned(live)) == shape(dict(expected.tables()))


def test_same_primary_keys(live, expected):
    got = {ref.key: t.primary_key_columns for ref, t in without_partitioned(live).items()}
    assert got == {ref.key: t.primary_key_columns for ref, t in expected.tables()}


def test_same_relationships(live, expected):
    def shape(diagram):
        return {
            r.name: (r.child.key, r.parent.key, r.foreign_key.columns, r.identifying,
                     str(r.parent_end), str(r.child_end), r.parallel_count)
            for r in derive_relationships(diagram)
        }

    assert shape(live) == shape(expected)


def test_postgres_specifics(live):
    shop = live.database(DB).schema("shop")
    kunde = shop.table("kunde")
    assert kunde.comment == "Kundenstamm"
    assert kunde.column("name").comment == "Anzeigename"
    assert kunde.column("id").auto_increment  # IDENTITY
    assert shop.table("artikel").column("id").auto_increment  # serial
    assert kunde.column("name").data_type == "character varying(100)"
    assert "ft_name" not in {i.name for i in kunde.indexes}  # reiner Ausdrucksindex
    names = {t.name for t in shop.tables}
    assert "protokoll" in names and "protokoll_2026" not in names  # keine Partitionen
    assert live.database(DB).charset == "UTF8"


def test_cross_schema_fk(live):
    (fk,) = live.database(DB).schema("shop").table("kunde").foreign_keys
    assert fk.referenced_table == TableRef(DB, "stammdaten", "land")


def test_unknown_database(live):
    profile = ConnectionProfile("pg-test", Dialect.POSTGRESQL, "localhost", None, USER)
    with PostgresAdapter(profile, PASSWORD) as adapter, pytest.raises(AdapterError):
        adapter.read_database("gibt_es_nicht")


def test_views(live):
    views = {ref.table: t for ref, t in live.tables() if t.is_view}
    assert set(views) == {"v_kunde_land", "v_bestellsumme", "v_kunde_bestellungen"}

    def deps(name):
        return {(r.schema, r.table) for r in views[name].view_dependencies}

    assert deps("v_kunde_land") == {("shop", "kunde"), ("stammdaten", "land")}
    assert deps("v_bestellsumme") == {("shop", "bestellung"), ("shop", "bestellposition")}
    assert deps("v_kunde_bestellungen") == {("shop", "v_kunde_land"), ("shop", "v_bestellsumme")}
    assert views["v_kunde_land"].comment == "Kunden mit Land"
    assert [c.name for c in views["v_bestellsumme"].columns] == ["bestellung_id", "kunde_id", "positionen"]
    text = views["v_kunde_land"].definition
    assert text.startswith("SELECT") and not text.endswith(";")
