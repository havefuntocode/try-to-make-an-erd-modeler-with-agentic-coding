"""Symbolleiste: die wichtigsten Aktionen als Icons (dieselben QActions wie in den Menüs).

Als Mixin für `MainWindow`. Erwartet `named_actions` (Name → QAction, beim
Bauen der Menüs gefüllt), `view`, `document`, `scene`, `editable_targets()`.
Die Icons liegen als SVG in `resources/toolbar`.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtGui import QAction, QActionGroup, QIcon, QKeySequence
from PySide6.QtWidgets import QMenu, QToolBar

TOOLBAR_ICON_DIR = Path(__file__).resolve().parent.parent / "resources" / "toolbar"
SETTINGS = ("ErdEditor", "ER-Diagramm-Editor")


def app_settings() -> QSettings:
    """Programmeinstellungen (in Tests auf eine temporäre INI-Datei umgelenkt)."""
    return QSettings(*SETTINGS)

# (Aktionsname, Icon); None = Trennstrich
TOOLBAR_LAYOUT: list[tuple[str, str] | None] = [
    ("new", "new"), ("open", "open"), ("save", "save"), ("read_database", "db_read"), ("reload", "reload"),
    None,
    ("undo", "undo"), ("redo", "redo"),
    None,
    ("new_database", "db_new"), ("new_table", "table_new"), ("new_view", "view_new"),
    ("relation_tool", "relation"), ("add_note", "note"),
    None,
    ("zoom_in", "zoom_in"), ("zoom_out", "zoom_out"), ("zoom_fit", "fit"), ("auto_layout", "auto_layout"),
    None,
    ("export_pdf", "pdf"), ("export_sql", "sql"),
]

STYLES = {
    "icons": (Qt.ToolButtonStyle.ToolButtonIconOnly, "Nur Symbole"),
    "beside": (Qt.ToolButtonStyle.ToolButtonTextBesideIcon, "Text neben den Symbolen"),
    "under": (Qt.ToolButtonStyle.ToolButtonTextUnderIcon, "Text unter den Symbolen"),
}


def toolbar_icon(name: str) -> QIcon:
    path = TOOLBAR_ICON_DIR / f"{name}.svg"
    return QIcon(str(path)) if path.is_file() else QIcon()


def tooltip(action: QAction) -> str:
    """Aktionstext ohne & und „…“, mit Tastenkürzel, z. B. „Neue Tabelle (Strg+Umschalt+N)“."""
    text = action.text().replace("&", "").replace(" …", "").replace("…", "").strip()
    keys = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
    return f"{text} ({keys})" if keys else text


class ToolbarMixin:
    toolbar: QToolBar

    def _create_toolbar(self) -> None:
        bar = QToolBar("Werkzeuge", self)
        bar.setObjectName("main_toolbar")
        bar.setIconSize(QSize(22, 22))
        bar.setMovable(False)
        for entry in TOOLBAR_LAYOUT:
            if entry is None:
                bar.addSeparator()
                continue
            name, icon = entry
            action = self.named_actions[name]
            action.setIcon(toolbar_icon(icon))
            action.setToolTip(tooltip(action))
            bar.addAction(action)
        bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        bar.customContextMenuRequested.connect(lambda pos: self.build_toolbar_menu().exec(bar.mapToGlobal(pos)))
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)
        self.toolbar = bar
        self.set_toolbar_style(str(QSettings(*SETTINGS).value("toolbar/style", "icons")), persist=False)

        # Beziehungs-Werkzeug: Knopf bleibt eingedrückt, solange das Werkzeug aktiv ist
        self.view.relation_mode_changed.connect(self.named_actions["relation_tool"].setChecked)

    def build_toolbar_menu(self) -> QMenu:
        """Rechtsklick auf die Leiste: Darstellung der Knöpfe."""
        menu = QMenu(self)
        group = QActionGroup(menu)
        current = self.toolbar.toolButtonStyle()
        for key, (style, text) in STYLES.items():
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(style == current)
            group.addAction(action)
            action.triggered.connect(lambda checked, k=key: self.set_toolbar_style(k))
        return menu

    def set_toolbar_style(self, key: str, persist: bool = True) -> None:
        style, _text = STYLES.get(key, STYLES["icons"])
        self.toolbar.setToolButtonStyle(style)
        if persist:
            QSettings(*SETTINGS).setValue("toolbar/style", key)

    def toggle_relation_tool(self, checked: bool) -> None:
        """Knopf/Menüpunkt „Beziehung zeichnen“ ein- oder ausschalten."""
        if checked:
            self.start_relation_tool()
        else:
            self.view.set_relation_mode(False)
        # abgelehnt (z. B. keine Modell-Datenbank) → Knopf wieder lösen
        self.named_actions["relation_tool"].setChecked(self.view.relation_mode)

    def _sync_toolbar(self) -> None:
        """Knöpfe aus-/eingrauen, je nachdem, was gerade möglich ist."""
        actions = getattr(self, "named_actions", {})
        if not actions:
            return
        diagram = self.document.diagram
        has_content = bool(diagram.databases)
        reloadable = any(db.connection_profile and self.document.connection(db.connection_profile)
                         for db in diagram.databases)
        states = {
            # Modellier-Aktionen immer anklickbar: ohne Modell-Datenbank bietet
            # `_choose_target` an, gleich eine anzulegen (statt still ausgegraut zu sein)
            "new_table": True, "new_view": True, "relation_tool": True,
            "reload": reloadable, "export_pdf": has_content, "export_sql": has_content,
            "auto_layout": bool(self.scene.entities), "add_note": has_content,
        }
        for name, enabled in states.items():
            if name in actions:
                actions[name].setEnabled(enabled)
