"""Symbolleiste: Icons, dieselben Aktionen wie die Menüs, Zustände, Beziehungs-Werkzeug."""

import pytest

from erd_editor.model import Column, Dialect, Index, IndexKind, Table, TableRef
from erd_editor.ui.toolbar import TOOLBAR_LAYOUT, toolbar_icon


@pytest.fixture
def window(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from erd_editor.main import MainWindow

    # Einstellungen landen über conftest._settings_in_tmp in einer temporären INI-Datei
    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, lambda *a: None)
    w = MainWindow()
    yield w
    w.setWindowModified(False)
    w.close()


def bar_actions(window):
    return [a for a in window.toolbar.actions() if not a.isSeparator()]


def test_all_icons_exist_and_render(qapp):
    for entry in TOOLBAR_LAYOUT:
        if entry is not None:
            icon = toolbar_icon(entry[1])
            assert not icon.isNull(), entry
            assert not icon.pixmap(22, 22).isNull()


def test_toolbar_uses_menu_actions(window):
    actions = bar_actions(window)
    assert len(actions) == sum(1 for e in TOOLBAR_LAYOUT if e is not None)
    menu_actions = {a for m in window.menuBar().actions() for a in m.menu().actions()}
    assert all(a in menu_actions for a in actions)  # kein Doppel – gleiche QAction wie im Menü
    assert all(not a.icon().isNull() for a in actions)


def test_tooltips_show_shortcut(window):
    tip = window.named_actions["new_table"].toolTip()
    assert tip.startswith("Neue Tabelle (") and "N)" in tip
    assert "&" not in tip and "…" not in tip
    assert window.named_actions["new_database"].toolTip() == "Neue Datenbank"


def test_states_follow_document(window):
    a = window.named_actions
    # Modellier-Aktionen bleiben anklickbar (ohne Modell-Datenbank wird nachgefragt)
    assert a["new_table"].isEnabled() and a["relation_tool"].isEnabled()
    assert not a["export_sql"].isEnabled() and not a["reload"].isEnabled()
    assert a["new_database"].isEnabled() and a["open"].isEnabled()
    window.create_database("lager", Dialect.MYSQL)
    assert a["new_table"].isEnabled() and a["new_view"].isEnabled() and a["export_sql"].isEnabled()
    assert not a["auto_layout"].isEnabled()  # noch keine Tabellen
    window.create_table(TableRef("lager", None, "regal"), Table("regal", [Column("id", "int", nullable=False)],
                                                                [Index("PRIMARY", IndexKind.PRIMARY, ("id",))]))
    assert a["auto_layout"].isEnabled()
    window.undo_stack.undo()
    window.undo_stack.undo()
    assert not a["export_sql"].isEnabled()  # auch nach Rückgängig richtig


def test_relation_tool_button_follows_mode(window):
    action = window.named_actions["relation_tool"]
    window.create_database("lager", Dialect.MYSQL)
    action.trigger()
    assert action.isChecked() and window.view.relation_mode
    window.view.set_relation_mode(False)  # z. B. Esc in der Ansicht
    assert not action.isChecked()
    action.trigger()
    action.trigger()
    assert not window.view.relation_mode


def test_relation_tool_refused_without_model_releases_button(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    action = window.named_actions["relation_tool"]
    window.toggle_relation_tool(True)  # ohne Modell-Datenbank, Rückfrage mit Nein beantwortet
    assert not action.isChecked() and not window.view.relation_mode


def test_toolbar_style_menu(window):
    from PySide6.QtCore import Qt

    menu = window.build_toolbar_menu()
    texts = [a.text() for a in menu.actions()]
    assert texts == ["Nur Symbole", "Text neben den Symbolen", "Text unter den Symbolen"]
    menu.actions()[2].trigger()
    assert window.toolbar.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextUnderIcon
    assert window.build_toolbar_menu().actions()[2].isChecked()
