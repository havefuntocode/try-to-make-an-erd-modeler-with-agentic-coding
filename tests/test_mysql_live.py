"""Live-Test gegen lokale MySQL- und MariaDB-Server mit eingespieltem Testschema.

Beide Server werden über denselben Adapter gelesen und müssen dasselbe
neutrale Modell liefern.

Voraussetzungen:
- tests/schema/mysql.sql ist im jeweiligen Server eingespielt
- Passwort im keyring (einmalig, empfohlen):
      .venv/Scripts/python -m keyring set erd-editor-test root           (MySQL)
      .venv/Scripts/python -m keyring set erd-editor-test root@mariadb   (MariaDB)
  oder in den Umgebungsvariablen ERD_MYSQL_PASSWORD / ERD_MARIADB_PASSWORD
- optional: ERD_MYSQL_USER (Standard root), ERD_MYSQL_HOST (localhost),
  ERD_MYSQL_PORT (3306), ERD_MARIADB_PORT (3307)

Ein Server ohne Passwort wird übersprungen.
"""

import os
from dataclasses import dataclass

import keyring
import pytest

from erd_editor.adapters.mysql import MySqlAdapter
from erd_editor.analysis import derive_relationships
from erd_editor.model import ConnectionProfile, Diagram, Dialect

SHOP, MASTER = "erd_test_shop", "erd_test_stammdaten"
KEYRING_SERVICE = "erd-editor-test"
USER = os.environ.get("ERD_MYSQL_USER", "root")
HOST = os.environ.get("ERD_MYSQL_HOST", "localhost")


@dataclass(frozen=True)
class LiveServer:
    """Ein lokaler Server der MySQL-Familie für die Live-Tests."""

    id: str
    port: int
    keyring_account: str
    password_env: str

    def password(self) -> str | None:
        if self.password_env in os.environ:
            return os.environ[self.password_env]
        try:
            return keyring.get_password(KEYRING_SERVICE, self.keyring_account)
        except keyring.errors.KeyringError:
            return None

    def profile(self) -> ConnectionProfile:
        return ConnectionProfile("test", Dialect.MYSQL, host=HOST, port=self.port, user=USER)


SERVERS = [
    LiveServer("mysql", int(os.environ.get("ERD_MYSQL_PORT", "3306")), USER, "ERD_MYSQL_PASSWORD"),
    LiveServer("mariadb", int(os.environ.get("ERD_MARIADB_PORT", "3307")), f"{USER}@mariadb",
               "ERD_MARIADB_PASSWORD"),
]


@pytest.fixture(scope="module", params=SERVERS, ids=[s.id for s in SERVERS])
def live(request) -> Diagram:
    server: LiveServer = request.param
    password = server.password()
    if password is None:
        pytest.skip(f"kein Passwort (keyring {KEYRING_SERVICE!r}/{server.keyring_account!r} "
                    f"oder {server.password_env})")
    with MySqlAdapter(server.profile(), password) as adapter:
        available = adapter.list_databases()
        if SHOP not in available or MASTER not in available:
            pytest.skip(f"Testschema nicht eingespielt in {server.id} (tests/schema/mysql.sql)")
        return Diagram([adapter.read_database(SHOP), adapter.read_database(MASTER)])


@pytest.fixture(scope="module")
def expected(schema_builder) -> Diagram:
    return schema_builder(SHOP, MASTER)


def test_live_schema_is_consistent(live):
    assert live.validate() == []


def test_database_meta(live):
    shop = live.database(SHOP)
    assert shop.charset == "utf8mb4"
    assert shop.server_version
    assert shop.connection_profile == "test"


def test_same_tables_and_columns(live, expected):
    def shape(diagram):
        return {
            ref.key: [(c.name, c.data_type, c.nullable) for c in table.columns]
            for ref, table in diagram.tables()
        }

    assert shape(live) == shape(expected)


def test_same_primary_keys(live, expected):
    def pks(diagram):
        return {ref.key: table.primary_key_columns for ref, table in diagram.tables()}

    assert pks(live) == pks(expected)


def test_same_relationships(live, expected):
    """Kernprüfung: identifizierend, Kardinalitäten, Sonderfälle stimmen überein."""

    def shape(diagram):
        return {
            r.name: (
                r.child.key,
                r.parent.key,
                r.foreign_key.columns,
                r.foreign_key.referenced_columns,
                r.identifying,
                str(r.parent_end),
                str(r.child_end),
                r.parallel_count,
            )
            for r in derive_relationships(diagram)
        }

    assert shape(live) == shape(expected)


def test_comments_and_details(live):
    kunde = live.find_table(next(r for r, _ in live.tables() if r.table == "kunde"))
    assert kunde.comment == "Kundenstamm"
    assert kunde.column("name").comment == "Anzeigename"
    assert kunde.column("name").default == ""
    assert kunde.column("id").auto_increment
    assert any(i.name == "ft_name" and i.kind.value == "FULLTEXT" for i in kunde.indexes)
