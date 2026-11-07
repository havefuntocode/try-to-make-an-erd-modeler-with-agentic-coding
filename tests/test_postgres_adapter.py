"""Offline-Tests für den PostgreSQL-Adapter: build_database() mit vorbereiteten Zeilen."""

import pytest

from erd_editor.adapters.base import AdapterError
from erd_editor.adapters.postgres import PostgresAdapter, build_database
from erd_editor.model import ConnectionProfile, Dialect, IndexKind, TableRef


def col(schema, table, name, type_, nullable=False, default=None, comment=None, identity=""):
    return dict(schema_name=schema, table_name=table, column_name=name, data_type=type_, nullable=nullable,
                column_default=default, comment=comment, identity=identity, generated="")


def idx(schema, table, name, seq, column, primary=False, unique=False, method="btree"):
    return dict(schema_name=schema, table_name=table, index_name=name, is_primary=primary, is_unique=unique,
                method=method, seq=seq, column_name=column)


def fk(schema, table, name, seq, column, ref_schema, ref_table, ref_column, upd="a", dele="a"):
    return dict(schema_name=schema, table_name=table, constraint_name=name, seq=seq, column_name=column,
                referenced_schema=ref_schema, referenced_table=ref_table, referenced_column=ref_column,
                update_rule=upd, delete_rule=dele)


@pytest.fixture
def db():
    return build_database(
        "erd",
        schemas=[dict(schema_name="leer"), dict(schema_name="public"), dict(schema_name="stamm")],
        tables=[
            dict(schema_name="public", table_name="kunde", comment="Kundenstamm"),
            dict(schema_name="public", table_name="artikel", comment=""),
            dict(schema_name="stamm", table_name="land", comment=None),
        ],
        columns=[
            col("public", "kunde", "id", "integer", identity="d"),
            col("public", "kunde", "name", "character varying(100)", default="''::character varying", comment="Anzeigename"),
            col("public", "kunde", "land_code", "character(2)", nullable=True),
            col("public", "artikel", "id", "integer", default="nextval('artikel_id_seq'::regclass)"),
            col("stamm", "land", "code", "character(2)"),
            col("public", "eine_view", "x", "integer"),  # keine Tabelle
        ],
        indexes=[
            idx("public", "kunde", "kunde_pkey", 1, "id", primary=True, unique=True),
            idx("public", "kunde", "ft_name", 1, None, method="gin"),  # nur Ausdruck → entfällt
            idx("public", "kunde", "uq_name_land", 1, "name", unique=True),
            idx("public", "kunde", "uq_name_land", 2, "land_code", unique=True),
            idx("public", "artikel", "artikel_pkey", 1, "id", primary=True, unique=True),
            idx("stamm", "land", "land_pkey", 1, "code", primary=True, unique=True),
        ],
        foreign_keys=[
            fk("public", "kunde", "fk_kunde_land", 1, "land_code", "stamm", "land", "code", upd="c", dele="n"),
        ],
        charset="UTF8",
        collation="de-DE",
        server_version="18.1",
        connection_profile="pg",
    )


def test_meta(db):
    assert db.dialect is Dialect.POSTGRESQL
    assert (db.charset, db.collation, db.server_version, db.connection_profile) == ("UTF8", "de-DE", "18.1", "pg")


def test_schemas_without_tables_are_dropped(db):
    assert [s.name for s in db.schemas] == ["public", "stamm"]


def test_tables_and_comments(db):
    public = db.schema("public")
    assert [t.name for t in public.tables] == ["kunde", "artikel"]
    assert public.table("kunde").comment == "Kundenstamm"
    assert public.table("artikel").comment is None


def test_columns(db):
    kunde = db.schema("public").table("kunde")
    assert [c.name for c in kunde.columns] == ["id", "name", "land_code"]
    assert kunde.column("id").auto_increment  # IDENTITY
    assert db.schema("public").table("artikel").column("id").auto_increment  # serial
    assert not kunde.column("name").auto_increment
    assert kunde.column("name").default == "''::character varying"
    assert kunde.column("name").comment == "Anzeigename"
    assert kunde.column("land_code").nullable and not kunde.column("id").nullable


def test_indexes(db):
    kunde = db.schema("public").table("kunde")
    assert {i.name: (i.kind, i.columns) for i in kunde.indexes} == {
        "kunde_pkey": (IndexKind.PRIMARY, ("id",)),
        "uq_name_land": (IndexKind.UNIQUE, ("name", "land_code")),
    }


def test_foreign_key_across_schemas(db):
    (fk_land,) = db.schema("public").table("kunde").foreign_keys
    assert fk_land.referenced_table == TableRef("erd", "stamm", "land")
    assert (fk_land.on_update, fk_land.on_delete) == ("CASCADE", "SET NULL")


def test_model_is_consistent(db):
    from erd_editor.model import Diagram

    assert Diagram([db]).validate() == []


def test_adapter_requires_connection():
    adapter = PostgresAdapter(ConnectionProfile("pg", Dialect.POSTGRESQL, "localhost"))
    with pytest.raises(AdapterError):
        adapter.list_databases()


def test_adapter_rejects_wrong_dialect():
    with pytest.raises(AdapterError):
        PostgresAdapter(ConnectionProfile("my", Dialect.MYSQL, "localhost"))
