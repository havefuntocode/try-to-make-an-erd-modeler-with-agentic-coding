"""Tests für den SQL-Server-Adapter: offline mit Zeilen, live per Windows-Anmeldung."""

import pytest

from erd_editor.adapters.base import AdapterError
from erd_editor.adapters.mssql import (
    MssqlAdapter,
    _strip_parens,
    build_database,
    connection_string,
    format_type,
    product_version,
)
from erd_editor.analysis import derive_relationships
from erd_editor.model import ConnectionProfile, Diagram, Dialect, IndexKind, TableRef


# ---------------------------------------------------------------- reine Funktionen


@pytest.mark.parametrize(
    "args, expected",
    [
        (("nvarchar", 200, 0, 0), "nvarchar(100)"),
        (("nvarchar", -1, 0, 0), "nvarchar(max)"),
        (("varchar", 50, 0, 0), "varchar(50)"),
        (("varbinary", -1, 0, 0), "varbinary(max)"),
        (("char", 2, 0, 0), "char(2)"),
        (("decimal", 9, 10, 2), "decimal(10,2)"),
        (("datetime2", 8, 27, 3), "datetime2(3)"),
        (("int", 4, 10, 0), "int"),
        (("uniqueidentifier", 16, 0, 0), "uniqueidentifier"),
        (("Postleitzahl", 10, 0, 0, True), "Postleitzahl"),
    ],
)
def test_format_type(args, expected):
    assert format_type(*args) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [("((0))", "0"), ("('')", "''"), ("(getdate())", "getdate()"), ("((1)+(2))", "(1)+(2)"), (None, None)],
)
def test_strip_parens(raw, expected):
    assert _strip_parens(raw) == expected


class TestConnectionString:
    def test_windows_auth_local(self):
        p = ConnectionProfile("w", Dialect.MSSQL, "localhost", None, None, {"auth": "windows"})
        s = connection_string(p, None, "shop", "ODBC Driver 18 for SQL Server")
        assert "Trusted_Connection=yes" in s and "PWD" not in s
        assert "DATABASE={shop}" in s and "TrustServerCertificate=yes" in s

    def test_sql_login_remote_with_port(self):
        p = ConnectionProfile("s", Dialect.MSSQL, "db.firma.de", 1444, "sa", {"auth": "sql"})
        s = connection_string(p, "ge}heim", None, "ODBC Driver 18 for SQL Server")
        assert "SERVER=db.firma.de,1444" in s
        assert "UID={sa}" in s and "PWD={ge}}heim}" in s  # } wird verdoppelt
        assert "TrustServerCertificate=no" in s  # entfernte Server: Zertifikat prüfen

    def test_default_is_windows_auth(self):
        p = ConnectionProfile("w", Dialect.MSSQL, "localhost")
        assert "Trusted_Connection=yes" in connection_string(p, None, None, "X")


def row(**kw):
    return kw


def test_build_database():
    db = build_database(
        "erd",
        schemas=[row(schema_name="dbo"), row(schema_name="shop")],
        tables=[row(schema_name="shop", table_name="kunde", comment="Kundenstamm"),
                row(schema_name="shop", table_name="land", comment=None)],
        columns=[
            row(schema_name="shop", table_name="kunde", column_name="id", type_name="int", is_user_type=False,
                max_length=4, precision=10, scale=0, nullable=False, is_identity=True, column_default=None, comment=None),
            row(schema_name="shop", table_name="kunde", column_name="name", type_name="nvarchar", is_user_type=False,
                max_length=200, precision=0, scale=0, nullable=False, is_identity=False, column_default="('')",
                comment="Anzeigename"),
            row(schema_name="shop", table_name="kunde", column_name="land", type_name="char", is_user_type=False,
                max_length=2, precision=0, scale=0, nullable=True, is_identity=False, column_default=None, comment=None),
            row(schema_name="shop", table_name="land", column_name="code", type_name="char", is_user_type=False,
                max_length=2, precision=0, scale=0, nullable=False, is_identity=False, column_default=None, comment=None),
        ],
        indexes=[
            row(schema_name="shop", table_name="kunde", index_name="pk", is_primary=True, is_unique=True,
                type_desc="CLUSTERED", seq=1, column_name="id"),
            row(schema_name="shop", table_name="land", index_name="pk_land", is_primary=True, is_unique=True,
                type_desc="CLUSTERED", seq=1, column_name="code"),
        ],
        foreign_keys=[
            row(schema_name="shop", table_name="kunde", constraint_name="fk_land", seq=1, column_name="land",
                referenced_schema="shop", referenced_table="land", referenced_column="code",
                update_rule="NO_ACTION", delete_rule="SET_NULL"),
        ],
        collation="Latin1_General_CI_AS",
        server_version="17.0.1145.4",
    )
    assert db.dialect is Dialect.MSSQL
    assert [s.name for s in db.schemas] == ["shop"]  # dbo ohne Tabellen entfällt
    kunde = db.schema("shop").table("kunde")
    assert kunde.comment == "Kundenstamm"
    assert kunde.column("id").auto_increment
    assert kunde.column("name").data_type == "nvarchar(100)"
    assert kunde.column("name").default == "''"
    assert kunde.primary_key.kind is IndexKind.PRIMARY
    (fk,) = kunde.foreign_keys
    assert fk.referenced_table == TableRef("erd", "shop", "land")
    assert (fk.on_update, fk.on_delete) == ("NO ACTION", "SET NULL")
    assert (db.charset, db.collation) == (None, "Latin1_General_CI_AS")
    assert Diagram([db]).validate() == []


def test_requires_connection():
    with pytest.raises(AdapterError):
        MssqlAdapter(ConnectionProfile("m", Dialect.MSSQL, "localhost")).list_databases()


# ---------------------------------------------------------------- live (Windows-Anmeldung)

DB = "erd_test_shop"


@pytest.fixture(scope="module")
def live():
    profile = ConnectionProfile("mssql-test", Dialect.MSSQL, "localhost", None, None, {"auth": "windows"})
    try:
        adapter = MssqlAdapter(profile)
        adapter.connect()
    except (AdapterError, ImportError) as exc:
        pytest.skip(f"kein SQL Server per Windows-Anmeldung erreichbar: {exc}")
    try:
        if DB not in adapter.list_databases():
            pytest.skip("Testschema nicht eingespielt (tests/schema/mssql.sql)")
        return Diagram([adapter.read_database(DB)])
    finally:
        adapter.close()


@pytest.fixture(scope="module")
def expected():
    from tests.conftest import build_schema_test_diagram

    return build_schema_test_diagram(DB)


class TestLive:
    def test_consistent(self, live):
        assert live.validate() == []

    def test_schemas(self, live):
        assert [s.name for s in live.database(DB).schemas] == ["shop", "stammdaten"]

    def test_same_columns(self, live, expected):
        def shape(d, skip=()):
            return {r.key: [(c.name, c.nullable) for c in t.columns if c.name not in skip] for r, t in d.tables()}

        assert shape(live, skip=("preis", "angelegt")) == shape(expected)

    def test_same_primary_keys(self, live, expected):
        def pks(d):
            return {r.key: t.primary_key_columns for r, t in d.tables()}

        assert pks(live) == pks(expected)

    def test_same_relationships(self, live, expected):
        def shape(d):
            return {
                r.name: (r.child.key, r.parent.key, r.foreign_key.columns, r.identifying,
                         str(r.parent_end), str(r.child_end), r.parallel_count)
                for r in derive_relationships(d)
            }

        assert shape(live) == shape(expected)

    def test_sql_server_specifics(self, live):
        shop = live.database(DB).schema("shop")
        kunde, artikel = shop.table("kunde"), shop.table("artikel")
        assert kunde.comment == "Kundenstamm"
        assert kunde.column("name").comment == "Anzeigename"
        assert kunde.column("name").data_type == "nvarchar(100)"
        assert kunde.column("name").default == "''"
        assert kunde.column("id").auto_increment
        assert artikel.column("preis").data_type == "decimal(10,2)"
        assert artikel.column("preis").default == "0"
        assert artikel.column("angelegt").data_type == "datetime2(3)"
        assert shop.table("kundenprofil").column("bio").data_type == "nvarchar(max)"
        # INCLUDE-Spalte ist keine Schlüsselspalte des Index
        ft = next(i for i in kunde.indexes if i.name == "ft_name")
        assert ft.columns == ("name",)
        db = live.database(DB)
        assert db.collation and db.server_version


def test_product_version():
    assert product_version("17.0.1145.4") == "2025 (17.0.1145.4)"
    assert product_version("16.0.4135.4") == "2022 (16.0.4135.4)"
    assert product_version("99.1") == "99.1"
    assert product_version(None) is None
