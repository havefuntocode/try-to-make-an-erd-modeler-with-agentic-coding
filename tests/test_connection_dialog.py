"""Tests für den Verbindungsdialog und das Einlesen über das Hauptfenster."""

import copy

import pytest
from PySide6.QtCore import Qt

from erd_editor.adapters.base import AdapterError, SchemaAdapter
from erd_editor.model import ConnectionProfile, Database, Dialect, Schema, Table, TableRef
from erd_editor.ui.connection_dialog import ConnectionDialog


class FakeAdapter(SchemaAdapter):
    """Liefert Datenbanken aus einem vorgegebenen Diagramm."""

    dialect = Dialect.MYSQL
    diagram = None
    password_ok = "geheim"
    opened: list = []

    def connect(self):
        if self._password != self.password_ok:
            raise AdapterError("Zugriff verweigert")
        FakeAdapter.opened.append(self.profile.name)

    def close(self):
        pass

    def list_databases(self):
        return [db.name for db in self.diagram.databases]

    def read_database(self, name):
        db = copy.deepcopy(self.diagram.database(name))
        db.connection_profile = self.profile.name
        return db


@pytest.fixture
def fake(test_schema, monkeypatch):
    FakeAdapter.diagram = test_schema
    FakeAdapter.opened = []
    # kein Zugriff auf den echten Tresor
    monkeypatch.setattr("erd_editor.persistence.credentials.get_password", lambda p: None)
    monkeypatch.setattr("erd_editor.persistence.credentials.set_password", lambda p, pw: None)
    return FakeAdapter


def check(dialog, *names):
    for i in range(dialog.database_list.count()):
        item = dialog.database_list.item(i)
        if item.text() in names:
            item.setCheckState(Qt.CheckState.Checked)


class TestDialog:
    def test_connect_lists_databases(self, qapp, fake):
        d = ConnectionDialog(adapter_factory=fake)
        d.password.setText("geheim")
        assert d.connect_to_server()
        assert [d.database_list.item(i).text() for i in range(d.database_list.count())] == ["shop", "stammdaten"]
        assert "2 Datenbank" in d.status.text()

    def test_wrong_password_shows_error(self, qapp, fake):
        d = ConnectionDialog(adapter_factory=fake)
        d.password.setText("falsch")
        assert not d.connect_to_server()
        assert "verweigert" in d.status.text()
        assert d.database_list.count() == 0

    def test_ok_only_with_selection(self, qapp, fake):
        from PySide6.QtWidgets import QDialogButtonBox

        d = ConnectionDialog(adapter_factory=fake)
        ok = d.buttons.button(QDialogButtonBox.StandardButton.Ok)
        d.password.setText("geheim")
        d.connect_to_server()
        assert not ok.isEnabled()
        check(d, "shop")
        assert ok.isEnabled()
        assert d.choice().databases == ["shop"]

    def test_profile_name_generated(self, qapp, fake):
        d = ConnectionDialog(adapter_factory=fake)
        d.host.setText("db.local")
        d.user.setText("app")
        assert d.profile() == ConnectionProfile("app@db.local", Dialect.MYSQL, "db.local", None, "app")
        d.port.setValue(3307)
        p = d.profile()
        assert (p.name, p.port) == ("app@db.local:3307", 3307)

    def test_saved_profile_prefills(self, qapp, fake):
        saved = ConnectionProfile("prod", Dialect.MYSQL, "srv", 3310, "leser")
        d = ConnectionDialog([saved], adapter_factory=fake)
        assert (d.name.text(), d.host.text(), d.port.value(), d.user.text()) == ("prod", "srv", 3310, "leser")
        assert d.profile() == saved

    def test_preselect_and_add_option(self, qapp, fake):
        d = ConnectionDialog(can_add=True, adapter_factory=fake, preselect=["stammdaten"])
        d.password.setText("geheim")
        d.connect_to_server()
        assert d.choice().databases == ["stammdaten"]
        assert d.choice().add_to_diagram
        assert not ConnectionDialog(can_add=False, adapter_factory=fake).choice().add_to_diagram

    def test_dialects_enabled(self, qapp):
        d = ConnectionDialog()
        model = d.dialect.model()
        assert all(model.item(i).isEnabled() for i in range(4))  # MySQL, MariaDB, PostgreSQL, SQL Server

    def test_windows_auth_only_for_sql_server(self, qapp):
        d = ConnectionDialog()
        d.show()
        assert d.windows_auth.isHidden()
        d.dialect.setCurrentIndex(d.dialect.findData(Dialect.MSSQL.value))
        assert not d.windows_auth.isHidden() and d.windows_auth.isChecked()
        assert not d.user.isEnabled() and not d.password.isEnabled()
        p = d.profile()
        assert (p.dialect, p.port, p.user, p.options, p.name) == (Dialect.MSSQL, None, None, {"auth": "windows"}, "windows@localhost")
        assert not d.choice().save_password
        d.close()

    def test_sql_login(self, qapp):
        d = ConnectionDialog()
        d.dialect.setCurrentIndex(d.dialect.findData(Dialect.MSSQL.value))
        d.windows_auth.setChecked(False)
        assert d.user.isEnabled() and d.password.isEnabled()
        assert d.user.text() == "sa"
        p = d.profile()
        assert (p.user, p.options, p.name) == ("sa", {"auth": "sql"}, "sa@localhost")

    def test_saved_windows_profile(self, qapp, fake):
        saved = ConnectionProfile("srv", Dialect.MSSQL, "srv", None, None, {"auth": "windows"})
        d = ConnectionDialog([saved], adapter_factory=fake)
        assert d.windows_auth.isChecked()
        assert d.profile() == saved

    def test_needs_password(self):
        from erd_editor.ui.connection_dialog import needs_password

        assert needs_password(ConnectionProfile("m", Dialect.MYSQL, "h"))
        assert not needs_password(ConnectionProfile("w", Dialect.MSSQL, "h", options={"auth": "windows"}))
        assert needs_password(ConnectionProfile("s", Dialect.MSSQL, "h", options={"auth": "sql"}))

    def test_live_sql_server_windows_auth(self, qapp):
        """Echter SQL Server per Windows-Anmeldung (übersprungen, wenn nicht erreichbar)."""
        d = ConnectionDialog()
        d.dialect.setCurrentIndex(d.dialect.findData(Dialect.MSSQL.value))
        if not d.connect_to_server():
            pytest.skip(f"SQL Server nicht erreichbar: {d.status.text()}")
        names = [d.database_list.item(i).text() for i in range(d.database_list.count())]
        assert "master" not in names

    def test_dialect_switch_sets_defaults(self, qapp):
        d = ConnectionDialog()
        d.dialect.setCurrentIndex(d.dialect.findData(Dialect.POSTGRESQL.value))
        assert (d.port.value(), d.user.text()) == (5432, "postgres")
        assert d.profile().dialect is Dialect.POSTGRESQL
        d.dialect.setCurrentIndex(d.dialect.findData(Dialect.MYSQL.value))
        assert (d.port.value(), d.user.text()) == (3306, "root")

    def test_dialect_switch_keeps_own_values(self, qapp):
        d = ConnectionDialog()
        d.port.setValue(6000)
        d.user.setText("ich")
        d.dialect.setCurrentIndex(d.dialect.findData(Dialect.POSTGRESQL.value))
        assert (d.port.value(), d.user.text()) == (6000, "ich")


class TestMainWindowReading:
    @pytest.fixture
    def window(self, qapp, fake):
        from erd_editor.main import MainWindow

        w = MainWindow()
        w.adapter_factory = fake
        yield w
        w.setWindowModified(False)
        w.close()

    PROFILE = ConnectionProfile("root@localhost", Dialect.MYSQL, "localhost", None, "root")

    def test_new_diagram(self, window):
        read = window.read_databases(self.PROFILE, ["shop"], "geheim", False)
        window.add_databases(self.PROFILE, read, add_to_diagram=False)
        assert [db.name for db in window.document.diagram.databases] == ["shop"]
        assert window.document.connections == [self.PROFILE]
        assert window.isWindowModified()

    def test_add_second_server_keeps_positions(self, window, fake, test_schema):
        window.add_databases(self.PROFILE, window.read_databases(self.PROFILE, ["shop"], "geheim", False), False)
        kunde = TableRef("shop", None, "kunde")
        window.scene.entities[kunde].setPos(4321, 99)

        other = ConnectionProfile("lager@srv2", Dialect.MYSQL, "srv2", None, "lager")
        fake.diagram = copy.deepcopy(test_schema)
        fake.diagram.databases.append(Database("lager", Dialect.MYSQL, [Schema(None, [Table("regal")])]))
        window.add_databases(other, window.read_databases(other, ["lager"], "geheim", False), True)

        assert [db.name for db in window.document.diagram.databases] == ["shop", "lager"]
        assert [c.name for c in window.document.connections] == ["root@localhost", "lager@srv2"]
        assert window.scene.entities[kunde].pos().x() == 4321
        assert window.document.diagram.database("lager").connection_profile == "lager@srv2"

    def test_add_existing_database_replaces_it(self, window):
        window.add_databases(self.PROFILE, window.read_databases(self.PROFILE, ["shop"], "geheim", False), False)
        window.add_databases(self.PROFILE, window.read_databases(self.PROFILE, ["shop"], "geheim", False), True)
        assert [db.name for db in window.document.diagram.databases] == ["shop"]
        assert len(window.document.connections) == 1

    def test_read_error_shows_message(self, window, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        messages = []
        monkeypatch.setattr(QMessageBox, "critical", lambda *a: messages.append(a[2]))
        assert window.read_databases(self.PROFILE, ["shop"], "falsch", False) is None
        assert "verweigert" in messages[0]

    def test_windows_auth_reads_without_password_prompt(self, window, monkeypatch, fake):
        from erd_editor.adapters.base import AdapterError

        class WinAdapter(fake):
            dialect = Dialect.MSSQL

            def connect(self):
                if self._password not in ("", None):
                    raise AdapterError("unerwartetes Passwort")

        window.adapter_factory = WinAdapter
        monkeypatch.setattr(window, "_password_for", lambda p: pytest.fail("Passwortabfrage bei Windows-Anmeldung"))
        profile = ConnectionProfile("windows@localhost", Dialect.MSSQL, "localhost", options={"auth": "windows"})
        assert [db.name for db in window.read_databases(profile, ["shop"])] == ["shop"]

    def test_save_password_only_when_wanted(self, window, monkeypatch):
        saved = []
        monkeypatch.setattr("erd_editor.persistence.credentials.set_password", lambda p, pw: saved.append(pw))
        window.read_databases(self.PROFILE, ["shop"], "geheim", False)
        assert saved == []
        window.read_databases(self.PROFILE, ["shop"], "geheim", True)
        assert saved == ["geheim"]
