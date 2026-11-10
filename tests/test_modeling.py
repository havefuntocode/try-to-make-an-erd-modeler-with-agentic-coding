"""Tests für das Modellieren im Hauptfenster (Version 3, erster Schritt)."""

import pytest
from PySide6.QtCore import QPointF

from erd_editor.model import Column, Dialect, Table, TableRef
from erd_editor.persistence.document import FILE_SUFFIX
from erd_editor.ui.table_dialog import TableDialog


def ref(table: str, db: str = "lager", schema=None) -> TableRef:
    return TableRef(db, schema, table)


@pytest.fixture
def window(qapp):
    from erd_editor.main import MainWindow

    w = MainWindow()
    yield w
    w.setWindowModified(False)
    w.close()


@pytest.fixture
def modeled(window):
    window.create_database("lager", Dialect.MYSQL)
    window.create_table(ref("regal"), Table("regal", [Column("id", "int", nullable=False)]))
    return window


class TestCreate:
    def test_new_database_shows_empty_frame(self, window):
        window.create_database("lager", Dialect.POSTGRESQL)
        db = window.document.diagram.database("lager")
        assert db.modeled and [s.name for s in db.schemas] == ["public"]
        assert "lager" in window.scene.containers
        assert "Modell · PostgreSQL" in window.scene.containers["lager"].info_text
        assert window.undo_stack.undoText() == "Datenbank „lager“ anlegen"

    def test_new_table_appears_and_undo(self, modeled):
        assert ref("regal") in modeled.scene.entities
        modeled.undo_stack.undo()
        assert ref("regal") not in modeled.scene.entities
        modeled.undo_stack.redo()
        assert ref("regal") in modeled.scene.entities

    def test_duplicate_database_is_refused(self, window, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        warnings = []
        monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a[2]))
        window.create_database("lager", Dialect.MYSQL)
        window.create_database("lager", Dialect.MYSQL)
        assert warnings and "gibt es schon" in warnings[0]
        assert len(window.document.diagram.databases) == 1

    def test_new_table_in_subdiagram_joins_it(self, modeled):
        modeled.add_subdiagram("Teil", [ref("regal")])
        modeled.create_table(ref("fach"), Table("fach", [Column("id", "int")]))
        assert ref("fach") in modeled.document.subdiagram("Teil").tables
        assert ref("fach") in modeled.scene.entities


class TestEdit:
    def test_rename_table_keeps_position_and_views(self, modeled):
        modeled.scene.entities[ref("regal")].setPos(400, 300)
        modeled.add_subdiagram("Teil", [ref("regal")])
        modeled.scene.entities[ref("regal")].setPos(77, 88)
        table = modeled.document.diagram.find_table(ref("regal"))
        modeled.update_table(ref("regal"), Table("lagerplatz", table.columns, table.indexes), {})
        assert modeled.document.subdiagram("Teil").tables == [ref("lagerplatz")]
        assert modeled.scene.entities[ref("lagerplatz")].pos() == QPointF(77, 88)
        modeled.activate_view(None)
        assert modeled.scene.entities[ref("lagerplatz")].pos() == QPointF(400, 300)

    def test_edit_via_double_click(self, modeled, monkeypatch):
        def fake_exec(dialog):
            dialog.name.setText("regal2")
            dialog.add_row(Column("hoehe", "int", nullable=True))
            return TableDialog.DialogCode.Accepted

        monkeypatch.setattr(TableDialog, "exec", fake_exec)
        modeled.scene.entity_activated.emit(ref("regal"))
        table = modeled.document.diagram.find_table(ref("regal2"))
        assert [c.name for c in table.columns] == ["id", "hoehe"]

    def test_real_double_click_opens_editor_and_releases_mouse(self, modeled, monkeypatch):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        opened = []
        monkeypatch.setattr(TableDialog, "exec", lambda d: opened.append(d._original.name) or 0)
        modeled.resize(1000, 700)
        modeled.show()
        item = modeled.scene.entities[ref("regal")]
        modeled.view.centerOn(item)
        pt = modeled.view.mapFromScene(item.sceneBoundingRect().center())
        QTest.mouseDClick(modeled.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pt)
        assert opened == []  # Dialog erst nach dem Mausereignis
        QTest.qWait(10)
        assert opened == ["regal"]
        assert modeled.scene.mouseGrabberItem() is None  # sonst träfe jeder weitere Doppelklick diese Box

    def test_read_database_is_read_only(self, window, test_schema, monkeypatch):
        window.load_diagram(test_schema)
        monkeypatch.setattr(TableDialog, "exec", lambda d: pytest.fail("Editor darf nicht öffnen"))
        window.edit_table(TableRef("shop", None, "kunde"))
        assert "schreibgeschützt" in window.statusBar().currentMessage()

    def test_take_over_makes_editable(self, window, test_schema):
        window.load_diagram(test_schema)
        window.take_over("shop")
        assert window.document.diagram.database("shop").modeled
        assert "Modell" in window.scene.containers["shop"].info_text
        window.undo_stack.undo()
        assert not window.document.diagram.database("shop").modeled


class TestDelete:
    def test_delete_selected(self, modeled, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
        modeled.scene.entities[ref("regal")].setSelected(True)
        modeled.delete_selected_tables()
        assert ref("regal") not in modeled.scene.entities
        modeled.undo_stack.undo()
        assert ref("regal") in modeled.scene.entities

    def test_delete_removes_from_subdiagram(self, modeled):
        modeled.create_table(ref("fach"), Table("fach", [Column("id", "int")]))
        modeled.add_subdiagram("Teil", [ref("regal"), ref("fach")])
        modeled.delete_tables([ref("fach")])
        assert modeled.document.subdiagram("Teil").tables == [ref("regal")]


class TestDialogs:
    def test_table_dialog_new_and_validation(self, qapp):
        d = TableDialog(Dialect.MSSQL, None, {"kunde"})
        assert [(c.name, c.data_type) for c, _, _ in d._rows()] == [("id", "int")]
        assert "Tabellennamen" in d.message.text()
        d.name.setText("kunde")
        assert "gibt es dort schon" in d.message.text()
        d.name.setText("artikel")
        assert d.message.text() == ""
        d.add_row(Column("id", "int"))
        assert "eindeutig" in d.message.text()

    def test_pk_forces_not_null(self, qapp):
        from PySide6.QtCore import Qt

        d = TableDialog(Dialect.MYSQL)
        row = d.add_row(Column("code", "char(2)", nullable=True))
        d.grid.item(row, 3).setCheckState(Qt.CheckState.Checked)  # PK
        assert d.grid.item(row, 2).checkState() == Qt.CheckState.Unchecked  # NULL
        d.name.setText("t")
        assert d.table().primary_key_columns == ("id", "code")

    def test_move_rows(self, qapp):
        d = TableDialog(Dialect.MYSQL)
        d.add_row(Column("b", "int"))
        d.grid.setCurrentCell(1, 0)
        d.move_row(-1)
        assert [c.name for c, _, _ in d._rows()] == ["b", "id"]

    def test_database_dialog(self, qapp):
        from erd_editor.ui.modeling import DatabaseDialog

        d = DatabaseDialog({"shop"})
        assert not d.schema.isEnabled()  # MySQL
        d.dialect.setCurrentIndex(2)
        assert d.schema.text() == "dbo" and d.schema.isEnabled()
        d.name.setText("shop")
        assert "gibt es schon" in d.message.text()


def test_save_and_open_model(modeled, tmp_path):
    from erd_editor.main import MainWindow

    path = tmp_path / f"modell{FILE_SUFFIX}"
    modeled._write(path)
    other = MainWindow()
    other.open_file(path)
    assert other.document.diagram.database("lager").modeled
    assert ref("regal") in other.scene.entities
    other.close()


def test_new_document(window, test_schema, monkeypatch):
    window.load_diagram(test_schema)
    window.setWindowModified(False)
    monkeypatch.setattr(window, "new_database_dialog", lambda: None)
    window.new_document()
    assert window.document.diagram.databases == []
    assert window.scene.entities == {}
    assert window.windowTitle().startswith("Unbenannt")
