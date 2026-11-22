"""MariaDB als eigener Dialekt: Modell, Adapter, Export, Dateien, Dialoge."""

import pytest

from erd_editor.adapters.base import AdapterError
from erd_editor.adapters.mysql import MySqlAdapter, build_database
from erd_editor.adapters.postgres import PostgresAdapter
from erd_editor.export.ddl import generate_script, quote
from erd_editor.model import Column, ConnectionProfile, Database, Diagram, Dialect, Index, IndexKind, Schema, Table
from erd_editor.model.editing import add_database
from erd_editor.model.types import COMMON_TYPES, DEFAULT_SCHEMAS, ID_TYPES
from erd_editor.persistence.document import DiagramDocument, document_from_dict, document_to_dict

MARIA_VERSION = "11.4.2-MariaDB"


def test_labels_and_family():
    assert [d.label for d in Dialect] == ["MySQL", "MariaDB", "PostgreSQL", "SQL Server"]
    assert Dialect.MARIADB.mysql_family and Dialect.MYSQL.mysql_family
    assert not Dialect.POSTGRESQL.mysql_family and not Dialect.MSSQL.mysql_family


def test_every_dialect_has_defaults():
    for dialect in Dialect:
        assert COMMON_TYPES[dialect] and dialect in ID_TYPES and dialect in DEFAULT_SCHEMAS


def test_mariadb_types():
    types = COMMON_TYPES[Dialect.MARIADB]
    assert {"uuid", "inet6", "inet4"} <= set(types)
    assert set(COMMON_TYPES[Dialect.MYSQL]) < set(types)  # alles aus MySQL, mehr nicht weniger
    assert "uuid" not in COMMON_TYPES[Dialect.MYSQL]


# ------------------------------------------------------------------ Adapter


def _args():
    return dict(tables=[{"table_name": "t", "comment": ""}], indexes=[], foreign_keys=[],
                columns=[{"table_name": "t", "column_name": "id", "column_type": "int(11)", "is_nullable": "NO",
                          "column_default": None, "comment": "", "extra": ""}])


def test_dialect_follows_server_version():
    assert build_database("d", server_version=MARIA_VERSION, **_args()).dialect is Dialect.MARIADB
    assert build_database("d", server_version="8.4.11", **_args()).dialect is Dialect.MYSQL


def test_mysql_adapter_accepts_both_profiles():
    for dialect in (Dialect.MYSQL, Dialect.MARIADB):
        MySqlAdapter(ConnectionProfile("p", dialect, "h"))  # ohne Verbindung
    with pytest.raises(AdapterError):
        MySqlAdapter(ConnectionProfile("p", Dialect.POSTGRESQL, "h"))
    with pytest.raises(AdapterError):
        PostgresAdapter(ConnectionProfile("p", Dialect.MARIADB, "h"))


# ------------------------------------------------------------------ Modellieren und Export


def test_modeled_mariadb_has_no_schema_level():
    d = add_database(Diagram(), "lager", Dialect.MARIADB, "ignoriert")
    db = d.database("lager")
    assert db.dialect is Dialect.MARIADB and db.modeled and [s.name for s in db.schemas] == [None]


def test_sql_export_like_mysql():
    assert quote(Dialect.MARIADB, "a`b") == "`a``b`"
    table = Table("geraet", [Column("id", "uuid", False), Column("ip", "inet6")],
                  [Index("PRIMARY", IndexKind.PRIMARY, ("id",))])
    script = generate_script(Diagram([Database("netz", Dialect.MARIADB, [Schema(None, [table])], modeled=True)]))
    assert "(MariaDB, 1 Tabellen)" in script
    assert "CREATE TABLE `geraet`" in script and "`id` uuid NOT NULL" in script and "ENGINE=InnoDB" in script


# ------------------------------------------------------------------ Dateien


def _file(dialect: str, server_version: str | None, modeled: bool = False) -> dict:
    db = Database("shop", Dialect.MYSQL, [Schema(None, [])], connection_profile="p",
                  server_version=server_version, modeled=modeled)
    profile = ConnectionProfile("p", Dialect.MYSQL, "localhost", 3307, "root")
    data = document_to_dict(DiagramDocument(Diagram([db]), connections=[profile]))
    data["schema"]["databases"][0]["dialect"] = dialect
    return data


def test_old_files_become_mariadb():
    doc = document_from_dict(_file("mysql", MARIA_VERSION))
    assert doc.diagram.database("shop").dialect is Dialect.MARIADB
    # Profil bleibt MySQL – sein Passwort im Tresor hängt am Dialekt; der Adapter bedient beide
    assert doc.connection("p").dialect is Dialect.MYSQL


def test_mysql_and_models_stay_mysql():
    assert document_from_dict(_file("mysql", "8.4.11")).diagram.database("shop").dialect is Dialect.MYSQL
    assert document_from_dict(_file("mysql", None, modeled=True)).diagram.database("shop").dialect is Dialect.MYSQL


def test_mariadb_roundtrip_in_file():
    data = _file("mariadb", MARIA_VERSION)
    assert document_to_dict(document_from_dict(data))["schema"]["databases"][0]["dialect"] == "mariadb"


# ------------------------------------------------------------------ Dialoge


def test_connection_dialog_offers_mariadb(qapp):
    from erd_editor.ui.connection_dialog import ConnectionDialog, default_adapter_factory

    d = ConnectionDialog()
    d.dialect.setCurrentIndex(d.dialect.findData(Dialect.POSTGRESQL.value))
    d.dialect.setCurrentIndex(d.dialect.findData(Dialect.MARIADB.value))
    assert (d.port.value(), d.user.text()) == (3306, "root")
    profile = d.profile()
    assert profile.dialect is Dialect.MARIADB and profile.port is None
    assert isinstance(default_adapter_factory(profile, None), MySqlAdapter)


def test_table_dialog_for_mariadb(qapp):
    from erd_editor.ui.table_dialog import INDEX_KINDS, TYPE, TableDialog

    assert IndexKind.FULLTEXT in INDEX_KINDS[Dialect.MARIADB]
    dialog = TableDialog(Dialect.MARIADB)
    assert dialog._pk_name() == "PRIMARY"
    assert dialog.grid.cellWidget(0, TYPE).findText("uuid") >= 0  # neue Tabelle: Zeile „id“


def test_database_dialog_lists_mariadb(qapp):
    from erd_editor.ui.modeling import DatabaseDialog

    d = DatabaseDialog(set())
    d.dialect.setCurrentIndex(d.dialect.findData(Dialect.MARIADB.value))
    assert d.chosen_dialect() is Dialect.MARIADB and not d.schema.isEnabled()
