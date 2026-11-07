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

    def test_other_dialects_disabled(self, qapp):
        d = ConnectionDialog()
        model = d.dialect.model()
        assert model.item(0).isEnabled()
        assert not model.item(1).isEnabled() and not model.item(2).isEnabled()


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

    def test_save_password_only_when_wanted(self, window, monkeypatch):
        saved = []
        monkeypatch.setattr("erd_editor.persistence.credentials.set_password", lambda p, pw: saved.append(pw))
        window.read_databases(self.PROFILE, ["shop"], "geheim", False)
        assert saved == []
        window.read_databases(self.PROFILE, ["shop"], "geheim", True)
        assert saved == ["geheim"]
