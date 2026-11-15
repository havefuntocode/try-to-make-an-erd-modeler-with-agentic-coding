"""Offline-Tests für den MySQL-Adapter: build_database() mit vorbereiteten Ergebniszeilen."""

import pytest

from erd_editor.adapters.base import AdapterError
from erd_editor.adapters.mysql import (
    MySqlAdapter,
    build_database,
    normalize_mariadb_default,
    normalize_mariadb_type,
)
from erd_editor.model import ConnectionProfile, Dialect, IndexKind, TableRef


def column(table, name, type_, nullable="NO", default=None, comment="", extra=""):
    return dict(
        table_name=table,
        column_name=name,
        column_type=type_,
        is_nullable=nullable,
        column_default=default,
        comment=comment,
        extra=extra,
    )


def index(table, name, seq, col, non_unique=1, type_="BTREE"):
    return dict(table_name=table, index_name=name, non_unique=non_unique, seq=seq, column_name=col, index_type=type_)


def fk(table, name, seq, col, ref_schema, ref_table, ref_col, upd="RESTRICT", dele="RESTRICT"):
    return dict(
        table_name=table,
        constraint_name=name,
        seq=seq,
        column_name=col,
        referenced_schema=ref_schema,
        referenced_table=ref_table,
        referenced_column=ref_col,
        update_rule=upd,
        delete_rule=dele,
    )


@pytest.fixture
def db():
    return build_database(
        "shop",
        tables=[
            dict(table_name="bestellung", comment=""),
            dict(table_name="kunde", comment="Kundenstamm"),
        ],
        columns=[
            column("bestellung", "id", "int"),
            column("bestellung", "liefer_kunde_id", "int", nullable="YES"),
            column("bestellung", "liefer_nr", "smallint", nullable="YES"),
            column("kunde", "id", "int unsigned", extra="auto_increment"),
            column("kunde", "name", b"varchar(100)", default="", comment="Anzeigename"),
            column("kunde", "land_code", "char(2)", nullable="YES"),
            column("kunde", "erstellt", "datetime", default="CURRENT_TIMESTAMP", extra="DEFAULT_GENERATED"),
            column("eine_view", "x", "int"),  # gehört zu keiner Basistabelle
        ],
        indexes=[
            index("bestellung", "PRIMARY", 1, "id", non_unique=0),
            index("bestellung", "fk_liefer", 1, "liefer_kunde_id"),
            index("bestellung", "fk_liefer", 2, "liefer_nr"),
            index("kunde", "PRIMARY", 1, "id", non_unique=0),
            index("kunde", "ft_name", 1, "name", type_="FULLTEXT"),
            index("kunde", "ix_funktional", 1, None),  # nur Ausdruck → entfällt
            index("kunde", "uq_name_land", 1, "name", non_unique=0),
            index("kunde", "uq_name_land", 2, None),  # Ausdrucksteil → wird ausgelassen
        ],
        foreign_keys=[
            fk("bestellung", "fk_liefer", 1, "liefer_kunde_id", "shop", "adresse", "kunde_id", dele="SET NULL"),
            fk("bestellung", "fk_liefer", 2, "liefer_nr", "shop", "adresse", "nr", dele="SET NULL"),
            fk("kunde", "fk_kunde_land", 1, "land_code", "stammdaten", "land", "code"),
        ],
        charset="utf8mb4",
        collation="utf8mb4_0900_ai_ci",
        server_version="8.4.3",
        connection_profile="lokal",
    )


def test_database_meta(db):
    assert db.name == "shop"
    assert db.dialect is Dialect.MYSQL
    assert (db.charset, db.collation, db.server_version) == ("utf8mb4", "utf8mb4_0900_ai_ci", "8.4.3")
    assert db.connection_profile == "lokal"
    assert [s.name for s in db.schemas] == [None]


def test_tables_and_comments(db):
    tables = {t.name: t for t in db.schemas[0].tables}
    assert set(tables) == {"bestellung", "kunde"}
    assert tables["kunde"].comment == "Kundenstamm"
    assert tables["bestellung"].comment is None


def test_columns(db):
    kunde = db.schema(None).table("kunde")
    assert [c.name for c in kunde.columns] == ["id", "name", "land_code", "erstellt"]
    assert kunde.column("id").auto_increment
    assert not kunde.column("id").nullable
    assert kunde.column("id").data_type == "int unsigned"
    name = kunde.column("name")
    assert name.data_type == "varchar(100)"  # Bytes dekodiert
    assert name.default == ""  # leerer Default bleibt erhalten
    assert name.comment == "Anzeigename"
    assert kunde.column("land_code").nullable
    assert kunde.column("land_code").default is None
    assert kunde.column("land_code").comment is None
    assert kunde.column("erstellt").default == "CURRENT_TIMESTAMP"


def test_indexes(db):
    kunde = db.schema(None).table("kunde")
    kinds = {i.name: (i.kind, i.columns) for i in kunde.indexes}
    assert kinds == {
        "PRIMARY": (IndexKind.PRIMARY, ("id",)),
        "ft_name": (IndexKind.FULLTEXT, ("name",)),
        "uq_name_land": (IndexKind.UNIQUE, ("name",)),
    }
    bestellung = db.schema(None).table("bestellung")
    assert bestellung.indexes[1].columns == ("liefer_kunde_id", "liefer_nr")
    assert bestellung.indexes[1].kind is IndexKind.INDEX


def test_foreign_keys(db):
    bestellung = db.schema(None).table("bestellung")
    (liefer,) = bestellung.foreign_keys
    assert liefer.columns == ("liefer_kunde_id", "liefer_nr")
    assert liefer.referenced_columns == ("kunde_id", "nr")
    assert liefer.referenced_table == TableRef("shop", None, "adresse")
    assert (liefer.on_update, liefer.on_delete) == ("RESTRICT", "SET NULL")

    (land,) = db.schema(None).table("kunde").foreign_keys
    assert land.referenced_table == TableRef("stammdaten", None, "land")


def test_adapter_rejects_wrong_dialect():
    profile = ConnectionProfile("pg", Dialect.POSTGRESQL, "localhost")
    with pytest.raises(AdapterError):
        MySqlAdapter(profile)


def test_query_without_connection_fails():
    adapter = MySqlAdapter(ConnectionProfile("lokal", Dialect.MYSQL, "localhost"))
    with pytest.raises(AdapterError):
        adapter.list_databases()


# ------------------------------------------------------------------ MariaDB


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("int(11)", "int"),
        ("bigint(20) unsigned", "bigint unsigned"),
        ("smallint(6)", "smallint"),
        ("tinyint(4)", "tinyint"),
        ("tinyint(1)", "tinyint(1)"),  # BOOLEAN, auch bei MySQL 8 mit Breite
        ("int(4) unsigned zerofill", "int(4) unsigned zerofill"),
        ("decimal(5,2)", "decimal(5,2)"),
        ("varchar(80)", "varchar(80)"),
    ],
)
def test_mariadb_type_normalization(raw, expected):
    assert normalize_mariadb_type(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("NULL", None),  # SQL-NULL kommt bei MariaDB als Text
        ("'NULL'", "NULL"),  # der Text 'NULL'
        ("''", ""),
        ("'it''s'", "it's"),
        ("5", "5"),
        ("current_timestamp()", "current_timestamp()"),
    ],
)
def test_mariadb_default_normalization(raw, expected):
    assert normalize_mariadb_default(raw) == expected


def test_build_database_detects_mariadb():
    columns = [
        {"table_name": "t", "column_name": "id", "column_type": "int(11)", "is_nullable": "NO",
         "column_default": "NULL", "comment": "", "extra": "auto_increment"},
        {"table_name": "t", "column_name": "name", "column_type": "varchar(10)", "is_nullable": "NO",
         "column_default": "'x'", "comment": "", "extra": ""},
    ]
    tables = [{"table_name": "t", "comment": ""}]
    args = dict(tables=tables, columns=columns, indexes=[], foreign_keys=[])

    maria = build_database("d", server_version="13.0.2-MariaDB", **args).schemas[0].tables[0]
    assert [(c.data_type, c.default) for c in maria.columns] == [("int", None), ("varchar(10)", "x")]

    # MySQL-Werte bleiben unverändert
    mysql = build_database("d", server_version="8.4.11", **args).schemas[0].tables[0]
    assert [(c.data_type, c.default) for c in mysql.columns] == [("int(11)", "NULL"), ("varchar(10)", "'x'")]
