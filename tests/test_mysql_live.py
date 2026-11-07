"""Live-Test gegen einen lokalen MySQL-Server mit eingespieltem Testschema.

Voraussetzungen:
- tests/schema/mysql.sql ist eingespielt
- Passwort entweder im keyring (einmalig, empfohlen):
      .venv/Scripts/python -m keyring set erd-editor-test root
  oder in der Umgebungsvariable ERD_MYSQL_PASSWORD
- optional: ERD_MYSQL_USER (Standard root), ERD_MYSQL_HOST (localhost), ERD_MYSQL_PORT (3306)

Ohne Passwort wird der Test übersprungen.
"""

import os

import keyring
import pytest

from erd_editor.adapters.mysql import MySqlAdapter
from erd_editor.analysis import derive_relationships
from erd_editor.model import ConnectionProfile, Diagram, Dialect

SHOP, MASTER = "erd_test_shop", "erd_test_stammdaten"
KEYRING_SERVICE = "erd-editor-test"
USER = os.environ.get("ERD_MYSQL_USER", "root")


def _password() -> str | None:
    if "ERD_MYSQL_PASSWORD" in os.environ:
        return os.environ["ERD_MYSQL_PASSWORD"]
    try:
        return keyring.get_password(KEYRING_SERVICE, USER)
    except keyring.errors.KeyringError:
        return None


PASSWORD = _password()

pytestmark = pytest.mark.skipif(
    PASSWORD is None,
    reason=f"kein Passwort (keyring {KEYRING_SERVICE!r}/{USER!r} oder ERD_MYSQL_PASSWORD)",
)


@pytest.fixture(scope="module")
def live() -> Diagram:
    profile = ConnectionProfile(
        "test",
        Dialect.MYSQL,
        host=os.environ.get("ERD_MYSQL_HOST", "localhost"),
        port=int(os.environ.get("ERD_MYSQL_PORT", "3306")),
        user=USER,
    )
    with MySqlAdapter(profile, PASSWORD) as adapter:
        available = adapter.list_databases()
        if SHOP not in available or MASTER not in available:
            pytest.skip("Testschema nicht eingespielt (tests/schema/mysql.sql)")
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
