"""Tests für Rückgängig/Wiederholen."""

import copy

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from erd_editor.model import Column, ColumnMode, ConnectionProfile, Dialect, Table, TableRef
from erd_editor.persistence.document import FILE_SUFFIX, DiagramDocument


def shop(table: str) -> TableRef:
    return TableRef("shop", None, table)


@pytest.fixture
def window(qapp, test_schema):
    from erd_editor.main import MainWindow

    w = MainWindow()
    w.view.resize(1600, 1200)
    w.show()
    w.load_diagram(test_schema)
    w.view.set_zoom(1.0)
    yield w
    w.setWindowModified(False)
    w.close()


def pos(window, table):
    return window.scene.entities[shop(table)].pos()


def drag(view, start: QPointF, end: QPointF) -> None:
    """Echte Mausgeste über die Ansicht (Drücken, zweimal Bewegen, Loslassen)."""
    view.centerOn(start)

    def send(kind, scene_pos, buttons):
        local = QPointF(view.mapFromScene(scene_pos))
        button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
        event = QMouseEvent(kind, local, view.viewport().mapToGlobal(local), button, buttons,
                            Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(view.viewport(), event)

    held = Qt.MouseButton.LeftButton
    send(QEvent.Type.MouseButtonPress, start, held)
    send(QEvent.Type.MouseMove, (start + end) / 2, held)
    send(QEvent.Type.MouseMove, end, held)
    send(QEvent.Type.MouseButtonRelease, end, Qt.MouseButton.NoButton)


def header_point(window, table) -> QPointF:
    item = window.scene.entities[shop(table)]
    return item.mapToScene(QPointF(30, 8))


class TestGestures:
    def test_drag_is_one_undo_step(self, window):
        before = pos(window, "kunde")
        drag(window.view, header_point(window, "kunde"), header_point(window, "kunde") + QPointF(120, 40))
        moved = pos(window, "kunde")
        assert moved != before
        assert window.undo_stack.count() == 1
        assert window.undo_stack.undoText() == "„kunde“ verschieben"

        window.undo_stack.undo()
        assert pos(window, "kunde") == before
        window.undo_stack.redo()
        assert pos(window, "kunde") == moved

    def test_click_without_move_records_nothing(self, window):
        p = header_point(window, "kunde")
        drag(window.view, p, p)
        assert window.undo_stack.count() == 0

    def test_lines_follow_undo(self, window):
        line = next(r for r in window.scene.relations if r.relationship.name == "fk_bestellung_kunde")
        points_before = list(line.points)
        drag(window.view, header_point(window, "kunde"), header_point(window, "kunde") + QPointF(0, 300))
        assert line.points != points_before
        window.undo_stack.undo()
        assert line.points == points_before

    def test_resize_frame_undo(self, window):
        container = window.scene.containers["stammdaten"]
        rect = container.rect
        start = QPointF(rect.right() - 2, rect.center().y())
        drag(window.view, start, start + QPointF(150, 0))
        assert container.rect.width() > rect.width() + 100
        assert window.undo_stack.undoText() == "Rahmen aufziehen"
        window.undo_stack.undo()
        assert container.rect == rect
        assert container.manual_rect is None


class TestMenuActions:
    def test_auto_layout_undo(self, window):
        window.scene.entities[shop("kunde")].setPos(3000, 3000)
        arranged = window.scene.current_layout()
        window._auto_layout()
        assert pos(window, "kunde") != QPointF(3000, 3000)
        window.undo_stack.undo()
        assert pos(window, "kunde") == QPointF(3000, 3000)
        assert window.scene.current_layout() == arranged

    def test_auto_layout_without_change_records_nothing(self, window):
        window._auto_layout()  # schon automatisch angeordnet
        assert window.undo_stack.count() == 0

    def test_display_scale_undo_and_menu(self, window):
        layout = window.scene.current_layout()
        window.set_display_scale(1.5)
        assert window.scale_actions[1.5].isChecked()
        window.undo_stack.undo()
        assert window.scene.display_scale == 1.0
        assert window.scale_actions[1.0].isChecked()
        assert window.scene.current_layout() == layout
        window.undo_stack.redo()
        assert window.scene.entities[shop("kunde")].scale() == 1.5

    def test_display_options_undo_and_menu(self, window):
        window.column_actions[ColumnMode.KEYS].trigger()
        assert window.undo_stack.undoText() == "Darstellung ändern"
        window.undo_stack.undo()
        assert window.scene.display_options.columns is ColumnMode.ALL
        assert window.column_actions[ColumnMode.ALL].isChecked()
        assert window.scene.entities[shop("kunde")].options.columns is ColumnMode.ALL

    def test_fit_frames_undo(self, window):
        container = window.scene.containers["stammdaten"]
        container.resize_to(container.rect.adjusted(0, 0, 200, 0))
        wide = container.rect
        window.fit_frames()
        assert container.manual_rect is None
        window.undo_stack.undo()
        assert container.rect == wide


class TestDocumentCommands:
    PROFILE = ConnectionProfile("root@localhost", Dialect.MYSQL, "localhost", None, "root")

    def test_reload_undo_restores_schema(self, window, test_schema, monkeypatch):
        for db in window.document.diagram.databases:
            db.connection_profile = self.PROFILE.name
        window.document.connections = [self.PROFILE]
        changed = copy.deepcopy(test_schema)
        changed.database("shop").schemas[0].tables.append(Table("neu", [Column("id", "int")]))
        monkeypatch.setattr(window, "read_databases", lambda profile, names, *a: [changed.database(n) for n in names])

        window.reload()
        assert shop("neu") in window.scene.entities
        assert window.undo_stack.undoText() == "Neu einlesen"
        window.undo_stack.undo()
        assert shop("neu") not in window.scene.entities
        window.undo_stack.redo()
        assert shop("neu") in window.scene.entities

    def test_new_document_clears_stack(self, window, test_schema):
        window.scene.entities[shop("kunde")].setPos(10, 10)
        window._auto_layout()
        assert window.undo_stack.count() == 1
        window.load_diagram(test_schema)
        assert window.undo_stack.count() == 0


class TestModifiedFlag:
    def test_undo_back_to_saved_state_is_unmodified(self, window, tmp_path):
        window._write(tmp_path / f"d{FILE_SUFFIX}")
        assert not window.isWindowModified()
        drag(window.view, header_point(window, "kunde"), header_point(window, "kunde") + QPointF(80, 0))
        assert window.isWindowModified()
        window.undo_stack.undo()
        assert not window.isWindowModified()
        window.undo_stack.redo()
        assert window.isWindowModified()

    def test_never_saved_diagram_stays_modified(self, window):
        window.set_display_scale(1.25)
        window.undo_stack.undo()
        assert window.isWindowModified()  # noch nie gespeichert


def test_edit_menu(window):
    menus = [a.text().replace("&", "") for a in window.menuBar().actions()]
    assert "Bearbeiten" in menus
    window.set_display_scale(1.25)
    assert window.undo_action.isEnabled()
    assert "Darstellungsgröße 125 %" in window.undo_action.text()
    assert not window.redo_action.isEnabled()
