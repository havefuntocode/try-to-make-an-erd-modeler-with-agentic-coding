"""Tests für das Beziehungs-Werkzeug: Zeichnen, Dialog, Anlegen, Löschen, Rückgängig."""

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication

from erd_editor.model import Column, Dialect, Index, IndexKind, Table, TableRef
from erd_editor.ui.relationship_dialog import RelationshipDialog


def ref(table: str) -> TableRef:
    return TableRef("lager", None, table)


def table(name: str) -> Table:
    return Table(name, [Column("id", "int", nullable=False)], [Index("PRIMARY", IndexKind.PRIMARY, ("id",))])


@pytest.fixture
def window(qapp):
    from erd_editor.main import MainWindow

    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w.create_database("lager", Dialect.MYSQL)
    w.create_table(ref("regal"), table("regal"))
    w.create_table(ref("fach"), table("fach"))
    w.scene.entities[ref("regal")].setPos(0, 0)
    w.scene.entities[ref("fach")].setPos(500, 0)
    w.view.set_zoom(1.0)
    w.view.centerOn(QPointF(350, 50))
    yield w
    w.setWindowModified(False)
    w.close()


def send(view, kind, scene_pos, buttons):
    local = QPointF(view.mapFromScene(scene_pos))
    button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
    event = QMouseEvent(kind, local, view.viewport().mapToGlobal(local), button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(view.viewport(), event)


def draw(view, start: QPointF, end: QPointF) -> None:
    held = Qt.MouseButton.LeftButton
    send(view, QEvent.Type.MouseButtonPress, start, held)
    send(view, QEvent.Type.MouseMove, (start + end) / 2, held)
    send(view, QEvent.Type.MouseMove, end, held)
    send(view, QEvent.Type.MouseButtonRelease, end, Qt.MouseButton.NoButton)


def center(window, name) -> QPointF:
    item = window.scene.entities[ref(name)]
    return item.mapRectToScene(item.rect).center()


class TestDrawing:
    def test_draw_from_child_to_parent(self, window, monkeypatch):
        seen = []
        monkeypatch.setattr(RelationshipDialog, "exec", lambda d: seen.append((d.child, d.parent_table)) or 1)
        window.start_relation_tool()
        assert window.view.relation_mode
        draw(window.view, center(window, "fach"), center(window, "regal"))
        assert seen == [(ref("fach"), ref("regal"))]
        assert not window.view.relation_mode  # Werkzeug ist danach wieder aus
        fach = window.document.diagram.find_table(ref("fach"))
        assert [fk.name for fk in fach.foreign_keys] == ["fk_fach_regal"]
        assert fach.column("regal_id") is not None
        assert len(window.scene.relations) == 1
        assert window.undo_stack.undoText() == "Beziehung „fk_fach_regal“ anlegen"

    def test_entities_do_not_move_while_drawing(self, window, monkeypatch):
        monkeypatch.setattr(RelationshipDialog, "exec", lambda d: 0)
        before = window.scene.entities[ref("fach")].pos()
        window.start_relation_tool()
        draw(window.view, center(window, "fach"), center(window, "fach") + QPointF(200, 300))
        assert window.scene.entities[ref("fach")].pos() == before

    def test_release_on_empty_space_does_nothing(self, window, monkeypatch):
        monkeypatch.setattr(RelationshipDialog, "exec", lambda d: pytest.fail("kein Dialog erwartet"))
        window.start_relation_tool()
        draw(window.view, center(window, "fach"), QPointF(300, 600))
        assert not window.view.relation_mode
        assert window.scene.relations == []
        assert not [i for i in window.scene.items() if type(i).__name__ == "QGraphicsLineItem"]

    def test_escape_cancels(self, window):
        window.start_relation_tool()
        QApplication.sendEvent(window.view, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
        assert not window.view.relation_mode

    def test_cancelled_dialog_changes_nothing(self, window, monkeypatch):
        monkeypatch.setattr(RelationshipDialog, "exec", lambda d: 0)
        count = window.undo_stack.count()
        window.on_relation_drawn(ref("fach"), ref("regal"))
        assert window.undo_stack.count() == count

    def test_tool_needs_model_database(self, qapp, test_schema, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        from erd_editor.main import MainWindow

        asked = []
        monkeypatch.setattr(QMessageBox, "question",
                            lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No)
        w = MainWindow()
        w.load_diagram(test_schema)
        w.start_relation_tool()
        assert not w.view.relation_mode
        assert "Modell-Datenbank" in asked[0] and "schreibgeschützt" in asked[0]
        w.setWindowModified(False)
        w.close()


class TestDialog:
    def test_preview_and_options(self, window):
        d = RelationshipDialog(window.document.diagram, ref("fach"), ref("regal"))
        assert "regal_id" in d.preview.text() and "fk_fach_regal" in d.preview.text()
        assert "NOT NULL" in d.preview.text()
        d.one_to_one.setChecked(True)
        assert "UNIQUE" in d.preview.text()
        d.mandatory.setChecked(False)
        assert d.options() == {"one_to_one": True, "identifying": False, "mandatory": False}
        d.identifying.setChecked(True)
        assert not d.mandatory.isEnabled()
        assert d.options()["mandatory"] is True  # identifizierend heißt NOT NULL
        assert "PK" in d.preview.text()

    def test_error_disables_ok(self, window):
        from PySide6.QtWidgets import QDialogButtonBox

        window.create_table(ref("log"), Table("log", [Column("text", "text")]))
        d = RelationshipDialog(window.document.diagram, ref("fach"), ref("log"))
        assert "Primärschlüssel" in d.preview.text()
        assert not d.box.button(QDialogButtonBox.StandardButton.Ok).isEnabled()


class TestCreateAndDelete:
    def test_identifying_one_to_one_and_undo(self, window):
        window.create_relationship(ref("fach"), ref("regal"), one_to_one=True, identifying=True)
        line = window.scene.relations[0].relationship
        assert line.identifying and line.is_one_to_one
        window.undo_stack.undo()
        assert window.scene.relations == []
        assert window.document.diagram.find_table(ref("fach")).column("regal_id") is None

    def test_delete_via_context_menu(self, window):
        window.create_relationship(ref("fach"), ref("regal"))
        menu = window.build_relation_menu(window.scene.relations[0])
        actions = {a.text(): a for a in menu.actions()}
        actions["Beziehung löschen (mit FK-Spalten)"].trigger()
        assert window.scene.relations == []
        assert window.document.diagram.find_table(ref("fach")).column("regal_id") is None
        window.undo_stack.undo()
        assert len(window.scene.relations) == 1

    def test_delete_keeping_columns(self, window):
        window.create_relationship(ref("fach"), ref("regal"))
        window.remove_relationship(ref("fach"), "fk_fach_regal", drop_columns=False)
        assert window.document.diagram.find_table(ref("fach")).column("regal_id") is not None

    def test_read_database_has_no_delete_entries(self, qapp, test_schema):
        from erd_editor.main import MainWindow

        w = MainWindow()
        w.load_diagram(test_schema)
        texts = [a.text() for a in w.build_relation_menu(w.scene.relations[0]).actions()]
        assert not any("löschen" in t for t in texts)
        w.setWindowModified(False)
        w.close()
