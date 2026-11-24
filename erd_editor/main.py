"""Einstiegspunkt des ER-Diagramm-Editors.

Aufrufe (bis es einen Verbindungsdialog gibt):

    python -m erd_editor.main --mysql root@localhost erd_test_shop erd_test_stammdaten
    python -m erd_editor.main mein_diagramm.erd.json
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QDate, QLibraryInfo, QLocale, QTimer, QTranslator
from PySide6.QtPrintSupport import QPrinter, QPrintPreviewDialog
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QIcon, QKeySequence, QUndoStack
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStyleFactory,
)

from erd_editor.adapters.base import AdapterError
from erd_editor.analysis import RelationshipOverride, describe_override, is_identifying, relationship_key
from erd_editor.diagram.scene import DiagramScene
from erd_editor.diagram.view import DiagramView
from erd_editor.export import Orientation, PageFormat, PageSpec, export_pdf, export_png, export_svg, print_scene
from erd_editor.export.render import page_layout
from erd_editor.notation import NOTATIONS
from erd_editor.model import ColumnMode, ConnectionProfile, Database, Diagram, Dialect, Layout
from erd_editor.persistence import credentials
from erd_editor.ui.commands import DocumentCommand, LayoutCommand
from erd_editor.ui.modeling import ModelingMixin
from erd_editor.ui.notes import NotesMixin
from erd_editor.ui.toolbar import ToolbarMixin
from erd_editor.ui.subdiagram_tabs import MAIN_VIEW_TITLE, SubdiagramTabsMixin
from erd_editor.ui.connection_dialog import ConnectionDialog, default_adapter_factory, needs_password
from erd_editor.persistence.document import (
    FILE_SUFFIX,
    DiagramDocument,
    DocumentError,
    load_document,
    save_document,
)

APP_NAME = "ER-Diagramm-Editor"
DISPLAY_SCALES = (1.0, 1.25, 1.5, 1.75, 2.0)

PDF_PAGES = {
    "A4 – Ausrichtung automatisch": PageSpec(PageFormat.A4, Orientation.AUTO),
    "A4 quer": PageSpec(PageFormat.A4, Orientation.LANDSCAPE),
    "A4 hoch": PageSpec(PageFormat.A4, Orientation.PORTRAIT),
    "A3 – Ausrichtung automatisch": PageSpec(PageFormat.A3, Orientation.AUTO),
    "A3 quer": PageSpec(PageFormat.A3, Orientation.LANDSCAPE),
    "A3 hoch": PageSpec(PageFormat.A3, Orientation.PORTRAIT),
}
PNG_RESOLUTIONS = {
    "Normal (1×, Bildschirm)": 1.0,
    "Hoch (2×, empfohlen für Artikel)": 2.0,
    "Sehr hoch (3×, für Druck und Bücher)": 3.0,
}
FILE_FILTER = f"ER-Diagramme (*{FILE_SUFFIX});;Alle Dateien (*)"
USAGE_HINT = f"Datei → Datenbank einlesen … (Strg+D) oder Datei → Öffnen … (*{FILE_SUFFIX})"


def parse_target(target: str) -> tuple[str, str, int | None]:
    """`user@host[:port]` zerlegen."""
    if "@" not in target:
        raise ValueError(f"Erwartet user@host[:port], erhalten: {target!r}")
    user, _, host_port = target.rpartition("@")
    host, _, port = host_port.partition(":")
    if not user or not host:
        raise ValueError(f"Erwartet user@host[:port], erhalten: {target!r}")
    return user, host, int(port) if port else None


def with_suffix(path: str) -> Path:
    """Dateiendung `.erd.json` ergänzen, falls sie fehlt."""
    p = Path(path)
    return p if p.name.endswith(FILE_SUFFIX) else p.with_name(p.name + FILE_SUFFIX)


def describe_changes(old: Diagram, new: Diagram) -> str:
    """Kurze Zusammenfassung für die Statuszeile nach dem Neu-Einlesen."""
    old_refs = {ref for ref, _ in old.tables()}
    new_refs = {ref for ref, _ in new.tables()}
    added, removed = len(new_refs - old_refs), len(old_refs - new_refs)
    if not added and not removed:
        return "Neu eingelesen – keine neuen oder entfernten Tabellen"
    return f"Neu eingelesen – {added} neu, {removed} entfernt"


class MainWindow(ModelingMixin, NotesMixin, ToolbarMixin, SubdiagramTabsMixin, QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.resize(1200, 800)
        self.scene = DiagramScene(self)
        self.view = DiagramView(self.scene, self)
        self.document = DiagramDocument()
        self.current_view = None  # None = „Gesamt“, sonst Name des Teildiagramms
        self._syncing_menu = False
        self.adapter_factory = default_adapter_factory  # in Tests austauschbar
        self.path: Path | None = None
        self.scene.layout_changed.connect(lambda: self.setWindowModified(True))
        self.undo_stack = QUndoStack(self)
        self.undo_stack.setUndoLimit(200)
        self.undo_stack.cleanChanged.connect(self._on_clean_changed)
        self.scene.gesture_finished.connect(self._push_gesture)
        self.scene.relation_context_requested.connect(self.relation_menu)
        self.scene.entity_activated.connect(self.edit_table)
        self.scene.note_activated.connect(self.edit_note)
        self.scene.note_context_requested.connect(self.note_menu)
        self.scene.entity_context_requested.connect(self.entity_menu)
        self.scene.canvas_context_requested.connect(self.canvas_menu)
        self.view.relation_drawn.connect(self.on_relation_drawn)
        self._init_tabs()
        self._create_menus()
        self._create_toolbar()
        self._sync_toolbar()
        self._update_title()
        self.statusBar().showMessage(USAGE_HINT)

    # ------------------------------------------------------------------ Menüs

    def _action(self, text: str, slot, shortcut=None) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcuts(shortcut if isinstance(shortcut, list) else [shortcut])
        action.triggered.connect(slot)
        return action

    def _named(self, name: str, action: QAction) -> QAction:
        """Aktion unter einem Namen merken (Symbolleiste, Zustände)."""
        if not hasattr(self, "named_actions"):
            self.named_actions: dict[str, QAction] = {}
        self.named_actions[name] = action
        return action

    def _create_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&Datei")
        file_menu.addAction(self._named("new", self._action("&Neu", self.new_document, QKeySequence.StandardKey.New)))
        file_menu.addAction(self._named("read_database", self._action("&Datenbank einlesen …", self.open_connection_dialog, QKeySequence("Ctrl+D"))))
        file_menu.addSeparator()
        file_menu.addAction(self._named("open", self._action("Ö&ffnen …", self.open_dialog, QKeySequence.StandardKey.Open)))
        file_menu.addAction(self._named("save", self._action("&Speichern", self.save, QKeySequence.StandardKey.Save)))
        file_menu.addAction(self._action("Speichern &unter …", self.save_as, QKeySequence("Ctrl+Shift+S")))
        file_menu.addSeparator()
        self.reload_action = self._named("reload", self._action("&Neu einlesen", self.reload, QKeySequence("F5")))
        file_menu.addAction(self.reload_action)
        file_menu.addSeparator()
        file_menu.addAction(self._action("&Drucken …", self.print_dialog, QKeySequence.StandardKey.Print))
        file_menu.addAction(self._named("export_pdf", self._action("Als &PDF exportieren …", self.export_pdf_dialog, QKeySequence("Ctrl+Shift+P"))))
        file_menu.addAction(self._action("Als P&NG exportieren …", self.export_png_dialog, QKeySequence("Ctrl+Shift+E")))
        file_menu.addAction(self._action("Als S&VG exportieren …", self.export_svg_dialog, QKeySequence("Ctrl+Shift+G")))
        file_menu.addAction(self._action("Als S&QL-Skript exportieren …", self.sql_export_dialog))
        file_menu.addSeparator()
        file_menu.addAction(self._action("&Beenden", self.close, QKeySequence.StandardKey.Quit))

        edit_menu = self.menuBar().addMenu("&Bearbeiten")
        undo_action = self.undo_stack.createUndoAction(self, "&Rückgängig")
        undo_action.setShortcuts(QKeySequence.StandardKey.Undo)
        redo_action = self.undo_stack.createRedoAction(self, "&Wiederholen")
        redo_action.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        edit_menu.addAction(undo_action)
        edit_menu.addAction(redo_action)
        self.undo_action, self.redo_action = undo_action, redo_action
        self._named("undo", undo_action)
        self._named("redo", redo_action)
        edit_menu.addSeparator()
        edit_menu.addAction(self._named("add_note", self._action("&Notiz hinzufügen …", lambda: self.add_note(), QKeySequence("Ctrl+Shift+K"))))
        edit_menu.addAction(self._action("Markiertes &löschen", self.delete_selection, QKeySequence.StandardKey.Delete))

        layout_menu = self.menuBar().addMenu("A&nordnen")
        layout_menu.addAction(self._named("auto_layout", self._action("&Automatisch anordnen", self._auto_layout, QKeySequence("Ctrl+L"))))
        layout_menu.addAction(self._action("&Rahmen an Inhalt anpassen", self.fit_frames))

        model_menu = self.menuBar().addMenu("&Modell")
        model_menu.addAction(self._named("new_database", self._action("Neue &Datenbank …", self.new_database_dialog)))
        model_menu.addAction(self._named("new_table", self._action("Neue &Tabelle …", self.new_table_dialog, QKeySequence("Ctrl+Shift+N"))))
        model_menu.addAction(self._named("new_view", self._action("Neue &View …", self.new_view_dialog, QKeySequence("Ctrl+Shift+V"))))
        model_menu.addAction(self._action("Tabelle &bearbeiten …", self._edit_selected_table, QKeySequence("Return")))
        model_menu.addAction(self._action("Tabellen &löschen …", self.delete_selected_tables))
        model_menu.addSeparator()
        relation_action = self._named("relation_tool", QAction("Be&ziehung zeichnen", self, checkable=True))
        relation_action.setShortcut(QKeySequence("Ctrl+B"))
        relation_action.triggered.connect(self.toggle_relation_tool)
        model_menu.addAction(relation_action)
        model_menu.addSeparator()
        model_menu.addAction(self._action("Als &Modell übernehmen …", self.take_over_dialog))
        model_menu.addAction(self._named("export_sql", self._action("&SQL-Skript erzeugen …", self.sql_export_dialog, QKeySequence("Ctrl+Shift+Q"))))

        sub_menu = self.menuBar().addMenu("&Teildiagramm")
        sub_menu.addAction(self._action("&Neu …", self.new_subdiagram, QKeySequence("Ctrl+T")))
        sub_menu.addAction(self._action("Aus &Auswahl", lambda: self.new_subdiagram(from_selection=True),
                                        QKeySequence("Ctrl+Shift+T")))
        sub_menu.addAction(self._action("&Bearbeiten …", lambda: self.edit_subdiagram()))
        sub_menu.addAction(self._action("&Löschen", lambda: self.delete_subdiagram()))
        sub_menu.addSeparator()
        sub_menu.addAction(self._action("Nächste Ansicht", lambda: self.step_view(+1), QKeySequence("Ctrl+PgDown")))
        sub_menu.addAction(self._action("Vorige Ansicht", lambda: self.step_view(-1), QKeySequence("Ctrl+PgUp")))

        view_menu = self.menuBar().addMenu("&Ansicht")
        view_menu.addAction(self._named("zoom_in", self._action("Vergrößern", self.view.zoom_in, [QKeySequence.StandardKey.ZoomIn, QKeySequence("Ctrl+=")])))
        view_menu.addAction(self._named("zoom_out", self._action("Verkleinern", self.view.zoom_out, QKeySequence.StandardKey.ZoomOut)))
        view_menu.addAction(self._named("zoom_fit", self._action("Ganzes Diagramm", self.view.zoom_to_fit, QKeySequence("Ctrl+0"))))
        view_menu.addAction(self._action("100 %", lambda: self.view.set_zoom(1.0), QKeySequence("Ctrl+1")))
        view_menu.addSeparator()

        # Darstellungsgröße: wirkt auf Boxen, Schrift und Symbole – auch im Druck
        size_menu = view_menu.addMenu("&Darstellungsgröße")
        self.scale_group = QActionGroup(self)
        self.scale_actions: dict[float, QAction] = {}
        for factor in DISPLAY_SCALES:
            action = QAction(f"{round(factor * 100)} %", self, checkable=True)
            action.triggered.connect(lambda checked, f=factor: self.set_display_scale(f))
            self.scale_group.addAction(action)
            size_menu.addAction(action)
            self.scale_actions[factor] = action
        size_menu.addSeparator()
        size_menu.addAction(self._action("Größer", lambda: self.step_display_scale(+1), QKeySequence("Ctrl+Shift++")))
        size_menu.addAction(self._action("Kleiner", lambda: self.step_display_scale(-1), QKeySequence("Ctrl+Shift+-")))
        self._sync_scale_menu()

        # Notation der Linienenden
        view_menu.addSeparator()
        notation_menu = view_menu.addMenu("&Notation")
        self.notation_group = QActionGroup(self)
        self.notation_actions: dict[str, QAction] = {}
        for key, cls in NOTATIONS.items():
            action = QAction(cls.name, self, checkable=True)
            action.triggered.connect(lambda checked, k=key: self.set_notation(k))
            self.notation_group.addAction(action)
            notation_menu.addAction(action)
            self.notation_actions[key] = action

        # Darstellungsoptionen: was die Boxen zeigen
        view_menu.addSeparator()
        columns_menu = view_menu.addMenu("&Spalten")
        self.column_group = QActionGroup(self)
        self.column_actions: dict[ColumnMode, QAction] = {}
        for mode, text, keys in [
            (ColumnMode.ALL, "&Alle Spalten", "Ctrl+Alt+1"),
            (ColumnMode.KEYS, "Nur &Schlüsselspalten (PK, FK)", "Ctrl+Alt+2"),
            (ColumnMode.NONE, "&Keine Spalten (nur Tabellenname)", "Ctrl+Alt+3"),
        ]:
            action = QAction(text, self, checkable=True)
            action.setShortcut(QKeySequence(keys))
            action.triggered.connect(lambda checked, m=mode: self.update_display_options(columns=m))
            self.column_group.addAction(action)
            columns_menu.addAction(action)
            self.column_actions[mode] = action
        self.option_actions: dict[str, QAction] = {}
        for field, text in [
            ("show_types", "&Datentypen"),
            ("show_indexes", "&Indexbereich"),
            ("show_comments", "Tabellen&kommentare"),
            ("show_checks", "&Checks im Indexbereich"),
        ]:
            action = QAction(text, self, checkable=True)
            action.toggled.connect(lambda checked, f=field: self.update_display_options(**{f: checked}))
            view_menu.addAction(action)
            self.option_actions[field] = action
        self._sync_display_menu()

    # ------------------------------------------------------------------ Darstellungsoptionen

    def update_display_options(self, **changes) -> None:
        """Einzelne Darstellungsoptionen ändern (columns, show_types, show_indexes, show_comments, show_checks)."""
        if self._syncing_menu:
            return
        options = replace(self.scene.display_options, **changes)
        result: list[bool] = []
        self.record_layout("Darstellung ändern", lambda: result.append(self.scene.set_display_options(options)))
        overlaps = bool(result and result[0])
        self._sync_display_menu()
        if overlaps:
            self.statusBar().showMessage("Einige Boxen überlappen jetzt – Anordnen → Automatisch anordnen (Strg+L)")
        else:
            self.statusBar().showMessage("Darstellung geändert")

    def _sync_display_menu(self) -> None:
        """Menüzustand an die Optionen der Szene anpassen (ohne Rückkopplung)."""
        o = self.scene.display_options
        self._syncing_menu = True
        try:
            for mode, action in self.column_actions.items():
                action.setChecked(mode is o.columns)
            for field, action in self.option_actions.items():
                action.setChecked(getattr(o, field))
        finally:
            self._syncing_menu = False

    # ------------------------------------------------------------------ Darstellungsgröße

    def set_display_scale(self, factor: float) -> None:
        self.record_layout(f"Darstellungsgröße {round(factor * 100)} %", lambda: self.scene.set_display_scale(factor))
        self._sync_scale_menu()
        self.view.zoom_to_fit()
        self.statusBar().showMessage(f"Darstellungsgröße {round(self.scene.display_scale * 100)} %")

    def step_display_scale(self, direction: int) -> None:
        """Zur nächsten Stufe in DISPLAY_SCALES wechseln."""
        current = self.scene.display_scale
        if direction > 0:
            target = next((f for f in DISPLAY_SCALES if f > current + 1e-9), DISPLAY_SCALES[-1])
        else:
            target = next((f for f in reversed(DISPLAY_SCALES) if f < current - 1e-9), DISPLAY_SCALES[0])
        self.set_display_scale(target)

    def _sync_scale_menu(self) -> None:
        current = self.scene.display_scale
        for factor, action in self.scale_actions.items():
            action.setChecked(abs(factor - current) < 1e-9)

    def _update_title(self) -> None:
        name = self.path.name if self.path else "Unbenannt"
        self.setWindowTitle(f"{name}[*] – {APP_NAME}")

    def _auto_layout(self) -> None:
        self.record_layout("Automatisch anordnen", self.scene.auto_layout)
        self.view.zoom_to_fit()

    def fit_frames(self) -> None:
        self.record_layout("Rahmen an Inhalt anpassen", self.scene.fit_frames_to_content)

    # ------------------------------------------------------------------ Überschreibungen

    def relation_menu(self, item, screen_pos) -> None:
        """Kontextmenü einer Linie anzeigen."""
        self.build_relation_menu(item).exec(screen_pos)

    def build_relation_menu(self, item) -> QMenu:
        """Kontextmenü einer Linie: Mindestkardinalität und identifizierend festlegen."""
        rel = item.relationship
        key = relationship_key(rel)
        current = self.document.overrides.get(key, RelationshipOverride())
        menu = QMenu(self)
        title = menu.addAction(f"Beziehung {rel.name}")
        title.setEnabled(False)
        menu.addSeparator()

        min_one = menu.addAction("Elternteil hat mindestens ein Kind")
        min_one.setCheckable(True)
        min_one.setChecked(rel.child_end.minimum == 1)
        min_one.triggered.connect(
            lambda checked: self.set_override(key, replace(current, child_min_one=True if checked else None))
        )

        ident_menu = menu.addMenu("Identifizierend")
        group = QActionGroup(ident_menu)
        derived = "ja" if self._derived_identifying(rel) else "nein"
        for value, text in [(None, f"Automatisch (abgeleitet: {derived})"), (True, "Ja (schwache Entität)"), (False, "Nein")]:
            action = ident_menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(current.identifying is value)
            group.addAction(action)
            action.triggered.connect(lambda checked, v=value: self.set_override(key, replace(current, identifying=v)))

        menu.addSeparator()
        reset = menu.addAction("Auf automatisch zurücksetzen")
        reset.setEnabled(not current.is_empty)
        reset.triggered.connect(lambda: self.set_override(key, RelationshipOverride()))

        # Löschen nur in Modell-Datenbanken
        db = self.document.diagram.database(rel.child.database)
        if db is not None and db.modeled:
            menu.addSeparator()
            menu.addAction("Beziehung löschen (mit FK-Spalten)").triggered.connect(
                lambda: self.remove_relationship(rel.child, rel.name, drop_columns=True))
            menu.addAction("Beziehung löschen (Spalten behalten)").triggered.connect(
                lambda: self.remove_relationship(rel.child, rel.name, drop_columns=False))
        return menu

    def _derived_identifying(self, rel) -> bool:
        """Aus dem Schema abgeleitet – unabhängig von einer Überschreibung."""
        table = self.document.diagram.find_table(rel.child)
        return rel.identifying if table is None else is_identifying(table, rel.foreign_key)

    def set_override(self, key, override: RelationshipOverride) -> None:
        """Überschreibung setzen (leer = entfernen) – als ein Rückgängig-Schritt."""
        old = self.document.overrides.get(key, RelationshipOverride())
        if old == override:
            return
        before = self.snapshot()
        if override.is_empty:
            self.document.overrides.pop(key, None)
        else:
            self.document.overrides[key] = override
        self.scene.set_overrides(self.document.overrides)
        text = describe_override(override) or "automatisch"
        self.record_document(before, f"„{key[1]}“: {text}")
        self.statusBar().showMessage(f"Beziehung {key[1]}: {text}")

    # ------------------------------------------------------------------ Rückgängig/Wiederholen

    def record_layout(self, text: str, action) -> None:
        """Layout-Aktion ausführen und als ein Schritt auf den Rückgängig-Stapel legen."""
        before = self.scene.current_layout()
        action()
        after = self.scene.current_layout()
        if after != before:
            self._push_layout(before, after, text)

    def _push_gesture(self, before, after, text: str) -> None:
        """Mausgeste der Szene (Verschieben, Rahmen aufziehen) als ein Schritt."""
        self._push_layout(before, after, text)

    def _push_layout(self, before, after, text: str) -> None:
        view = self.current_view
        self.undo_stack.push(LayoutCommand(self.scene, before, after, text, self._sync_menus,
                                           prepare=lambda: self.activate_view(view)))

    def _edit_selected_table(self) -> None:
        refs = self.selected_refs()
        if len(refs) == 1:
            self.edit_table(refs[0])
        else:
            self.statusBar().showMessage("Bitte genau eine Tabelle markieren")

    def _sync_menus(self) -> None:
        self._sync_scale_menu()
        self._sync_display_menu()
        self._sync_notation_menu()
        self._sync_toolbar()

    def set_notation(self, key: str) -> None:
        self.record_layout(f"Notation {NOTATIONS[key].name}", lambda: self.scene.set_notation(key))
        self._sync_notation_menu()
        self.statusBar().showMessage(f"Notation: {NOTATIONS[key].name}")

    def _sync_notation_menu(self) -> None:
        for key, action in self.notation_actions.items():
            action.setChecked(key == self.scene.notation.key)

    def _on_clean_changed(self, clean: bool) -> None:
        """Sternchen im Titel: ungespeichert, solange der Stapel nicht am gespeicherten Stand ist.

        Ein noch nie gespeichertes Diagramm mit Inhalt gilt immer als geändert.
        """
        never_saved = self.path is None and bool(self.scene.entities)
        self.setWindowModified(not clean or never_saved)

    # ------------------------------------------------------------------ Anzeigen

    def show_document(self, document: DiagramDocument, use_layout: bool) -> None:
        self.undo_stack.clear()  # neues Dokument: nichts mehr rückgängig zu machen
        self.document = document
        if not use_layout:  # neu eingelesen: automatisch anordnen, Größe und Optionen der Sitzung behalten
            document.layout = Layout(scale=self.scene.display_scale, display=self.scene.display_options,
                                     notation=self.scene.notation.key)
        self.current_view = None
        self.show_view(None)
        problems = document.diagram.validate()
        views = sum(1 for _, t in document.diagram.tables() if t.is_view)
        tables = sum(1 for _ in document.diagram.tables()) - views
        msg = f"{tables} Tabellen" + (f", {views} View{'' if views == 1 else 's'}" if views else "")
        msg += f" aus {len(document.diagram.databases)} Datenbank(en)"
        if problems:
            msg += f" – {len(problems)} Hinweis(e), z. B.: {problems[0]}"
        self.statusBar().showMessage(msg)

    def load_diagram(self, diagram: Diagram) -> None:
        """Diagramm ohne Datei anzeigen (automatisch angeordnet)."""
        self.show_document(DiagramDocument(diagram), use_layout=False)

    # ------------------------------------------------------------------ Datenbank

    def _password_for(self, profile: ConnectionProfile) -> tuple[str | None, bool]:
        """Passwort aus dem keyring oder per Dialog; zweiter Wert: neu eingegeben."""
        password = credentials.get_password(profile)
        if password is not None:
            return password, False
        password, ok = QInputDialog.getText(
            self, "Passwort", f"Passwort für {profile.user}@{profile.host}:", QLineEdit.EchoMode.Password
        )
        return (password, True) if ok else (None, False)

    def read_databases(
        self,
        profile: ConnectionProfile,
        names: list[str],
        password: str | None = None,
        save_password: bool | None = None,
    ) -> list[Database] | None:
        """Datenbanken eines Profils einlesen; None bei Abbruch oder Fehler.

        Ohne `password` wird es aus dem keyring geholt oder abgefragt.
        `save_password=None` heißt: bei neu eingegebenem Passwort nachfragen.
        """
        newly_entered = False
        if password is None and not needs_password(profile):
            password, save_password = "", False  # Windows-Anmeldung
        if password is None:
            password, newly_entered = self._password_for(profile)
            if password is None:
                return None
        try:
            with self.adapter_factory(profile, password) as adapter:
                databases = [adapter.read_database(name) for name in names]
        except AdapterError as exc:
            QMessageBox.critical(self, "Fehler beim Einlesen", str(exc))
            return None
        if save_password is None and newly_entered:
            answer = QMessageBox.question(
                self, "Passwort speichern?", "Passwort in der Windows-Anmeldeinformationsverwaltung speichern?"
            )
            save_password = answer == QMessageBox.StandardButton.Yes
        if save_password:
            credentials.set_password(profile, password)
        return databases

    def open_connection_dialog(self) -> None:
        """Datei → Datenbank einlesen …"""
        has_diagram = bool(self.document.diagram.databases)
        dialog = ConnectionDialog(
            self.document.connections,
            can_add=has_diagram,
            adapter_factory=self.adapter_factory,
            preselect=[db.name for db in self.document.diagram.databases],
            parent=self,
        )
        if dialog.exec() != ConnectionDialog.DialogCode.Accepted:
            return
        choice = dialog.choice()
        if not choice.add_to_diagram and not self._confirm_discard():
            return
        read = self.read_databases(choice.profile, choice.databases, choice.password, choice.save_password)
        if read is not None:
            self.add_databases(choice.profile, read, choice.add_to_diagram)

    def add_databases(self, profile: ConnectionProfile, databases: list[Database], add_to_diagram: bool) -> None:
        """Eingelesene Datenbanken anzeigen – neu oder zum Diagramm hinzu.

        Beim Hinzufügen ersetzen gleichnamige Datenbanken die vorhandenen;
        Positionen bekannter Tabellen bleiben erhalten.
        """
        if not add_to_diagram:
            self.path = None
            self.show_document(DiagramDocument(Diagram(databases), connections=[profile]), use_layout=False)
            self._update_title()
            self.setWindowModified(True)
            return
        before = self.snapshot()
        old = self.document.diagram
        fresh = {db.name: db for db in databases}
        merged = [fresh.pop(db.name, db) for db in old.databases] + list(fresh.values())
        self.document.connections = [c for c in self.document.connections if c.name != profile.name] + [profile]
        self.document.diagram = Diagram(merged)
        self.prune_subdiagrams(self.document.diagram)
        self.show_view(self.current_view, keep_view=True)
        self.record_document(before, "Datenbanken hinzufügen")
        self.statusBar().showMessage(describe_changes(old, self.document.diagram))
        self.setWindowModified(True)

    def load_mysql(self, target: str, databases: list[str]) -> None:
        try:
            user, host, port = parse_target(target)
        except ValueError as exc:
            QMessageBox.critical(self, "Ungültige Verbindung", str(exc))
            return
        profile = ConnectionProfile(f"{user}@{host}", Dialect.MYSQL, host, port, user)
        read = self.read_databases(profile, databases)
        if read is not None:
            self.add_databases(profile, read, add_to_diagram=False)

    def reload(self) -> None:
        """Alle Datenbanken neu einlesen; Positionen bleiben erhalten."""
        old = self.document.diagram
        if not old.databases:
            self.statusBar().showMessage("Nichts zum Neu-Einlesen – zuerst eine Datenbank einlesen oder öffnen")
            return
        by_profile: dict[str, list[str]] = {}
        for db in old.databases:
            if db.connection_profile and self.document.connection(db.connection_profile):
                by_profile.setdefault(db.connection_profile, []).append(db.name)

        fresh: dict[str, Database] = {}
        for profile_name, names in by_profile.items():
            read = self.read_databases(self.document.connection(profile_name), names)
            if read is None:
                return  # Abbruch oder Fehler: alles bleibt wie es war
            fresh.update({db.name: db for db in read})

        # Reihenfolge beibehalten; Datenbanken ohne Profil bleiben unverändert
        new_diagram = Diagram([fresh.get(db.name, db) for db in old.databases])
        before = self.snapshot()
        self.document.diagram = new_diagram
        self.prune_subdiagrams(new_diagram)
        self.show_view(self.current_view, keep_view=True)
        self.record_document(before, "Neu einlesen")
        self.statusBar().showMessage(describe_changes(old, new_diagram))
        self.setWindowModified(True)

    # ------------------------------------------------------------------ Datei

    def open_dialog(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Diagramm öffnen", str(self._dialog_dir()), FILE_FILTER)
        if path:
            self.open_file(path)

    def open_file(self, path: str | Path) -> bool:
        try:
            document = load_document(path)
        except (OSError, DocumentError) as exc:
            QMessageBox.critical(self, "Öffnen fehlgeschlagen", f"{path}\n\n{exc}")
            return False
        self.path = Path(path)
        self.show_document(document, use_layout=True)
        self._update_title()
        self.setWindowModified(False)
        return True

    def save(self) -> bool:
        if self.path is None:
            return self.save_as()
        return self._write(self.path)

    def save_as(self) -> bool:
        suggestion = self.path or self._dialog_dir() / f"diagramm{FILE_SUFFIX}"
        path, _ = QFileDialog.getSaveFileName(self, "Diagramm speichern", str(suggestion), FILE_FILTER)
        if not path:
            return False
        return self._write(with_suffix(path))

    def _write(self, path: Path) -> bool:
        self.store_view_state()
        try:
            save_document(self.document, path)
        except OSError as exc:
            QMessageBox.critical(self, "Speichern fehlgeschlagen", f"{path}\n\n{exc}")
            return False
        self.path = path
        self._update_title()
        self.undo_stack.setClean()
        self.setWindowModified(False)
        self.statusBar().showMessage(f"Gespeichert: {path}")
        return True

    # ------------------------------------------------------------------ Drucken und Export

    def footer_text(self) -> str:
        name = self.path.name if self.path else "Unbenanntes Diagramm"
        view = f" · {self.current_view}" if self.current_view else ""
        return f"{name}{view} · {QDate.currentDate().toString('dd.MM.yyyy')}"

    def _has_content(self) -> bool:
        if not self.scene.entities:
            self.statusBar().showMessage("Nichts zu drucken oder exportieren – zuerst eine Datenbank einlesen")
            return False
        return True

    def _export_path(self, suffix: str) -> Path:
        """Vorschlag: Name der Diagrammdatei mit neuer Endung."""
        if self.path:
            return self.path.with_name(self.path.name.removesuffix(FILE_SUFFIX) + suffix)
        return self._dialog_dir() / f"diagramm{suffix}"

    def print_dialog(self) -> None:
        if not self._has_content():
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPageLayout(page_layout(self.scene, PageSpec()))
        preview = QPrintPreviewDialog(printer, self)
        preview.setWindowTitle("Druckvorschau")
        preview.paintRequested.connect(lambda p: print_scene(self.scene, p, self.footer_text()))
        preview.resize(1000, 750)
        preview.exec()

    def export_pdf_dialog(self) -> None:
        if not self._has_content():
            return
        choice, ok = QInputDialog.getItem(self, "Als PDF exportieren", "Seitenformat:", list(PDF_PAGES), 0, False)
        if not ok:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Als PDF exportieren", str(self._export_path(".pdf")), "PDF (*.pdf)")
        if not path:
            return
        path = path if path.lower().endswith(".pdf") else path + ".pdf"
        try:
            export_pdf(self.scene, path, PDF_PAGES[choice], self.path.name if self.path else "", self.footer_text())
        except OSError as exc:
            QMessageBox.critical(self, "Export fehlgeschlagen", str(exc))
            return
        self.statusBar().showMessage(f"PDF gespeichert: {path}")

    def export_png_dialog(self) -> None:
        if not self._has_content():
            return
        choice, ok = QInputDialog.getItem(self, "Als PNG exportieren", "Auflösung:", list(PNG_RESOLUTIONS), 1, False)
        if not ok:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Als PNG exportieren", str(self._export_path(".png")), "PNG-Bild (*.png)")
        if not path:
            return
        path = path if path.lower().endswith(".png") else path + ".png"
        try:
            size = export_png(self.scene, path, PNG_RESOLUTIONS[choice])
        except OSError as exc:
            QMessageBox.critical(self, "Export fehlgeschlagen", str(exc))
            return
        self.statusBar().showMessage(f"PNG gespeichert ({size.width()} × {size.height()} Pixel): {path}")

    def export_svg_dialog(self) -> None:
        if not self._has_content():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Als SVG exportieren", str(self._export_path(".svg")), "SVG-Grafik (*.svg)")
        if not path:
            return
        path = path if path.lower().endswith(".svg") else path + ".svg"
        title = self.footer_text().rsplit(" · ", 1)[0]  # Datei und Ansicht, ohne Datum
        try:
            size = export_svg(self.scene, path, title)
        except OSError as exc:
            QMessageBox.critical(self, "Export fehlgeschlagen", str(exc))
            return
        self.statusBar().showMessage(f"SVG gespeichert ({size.width()} × {size.height()}): {path}")

    def _dialog_dir(self) -> Path:
        return self.path.parent if self.path else Path.cwd()

    def _confirm_discard(self) -> bool:
        """Bei ungespeicherten Änderungen nachfragen; True = weitermachen."""
        if not self.isWindowModified():
            return True
        buttons = QMessageBox.StandardButton
        answer = QMessageBox.question(
            self,
            "Ungespeicherte Änderungen",
            "Das Diagramm wurde geändert. Änderungen speichern?",
            buttons.Save | buttons.Discard | buttons.Cancel,
        )
        if answer == buttons.Save:
            return self.save()
        return answer == buttons.Discard

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()


APP_ICON = Path(__file__).resolve().parent / "resources" / "app.ico"
# Eigene AppUserModelID: Die Taskleiste zeigt dann unser Symbol statt des Python-Symbols
# und gruppiert die Fenster nicht unter python.exe (Format Firma.Produkt.Teil).
APP_USER_MODEL_ID = "ErdEditor.ErDiagrammEditor.App"


def app_icon() -> QIcon:
    """Anwendungssymbol (alle Größen aus der .ico); leer, falls die Datei fehlt."""
    return QIcon(str(APP_ICON)) if APP_ICON.is_file() else QIcon()


def set_app_user_model_id(app_id: str = APP_USER_MODEL_ID) -> bool:
    """Unter Windows eine eigene AppUserModelID für diesen Prozess setzen.

    Muss vor dem Anlegen des ersten Fensters geschehen. Auf anderen
    Systemen (oder wenn der Aufruf scheitert) passiert nichts.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id) == 0  # S_OK
    except (AttributeError, OSError):
        return False


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    parser = argparse.ArgumentParser(prog="erd_editor")
    parser.add_argument("--mysql", metavar="USER@HOST[:PORT]", help="MySQL-Server, von dem gelesen wird")
    parser.add_argument("items", nargs="*", metavar="DATENBANK|DATEI",
                        help="mit --mysql: einzulesende Datenbanken, sonst eine Diagrammdatei")
    args = parser.parse_args(argv[1:])

    set_app_user_model_id()  # vor dem ersten Fenster, sonst gruppiert Windows unter python.exe
    app = QApplication(argv)
    app.setWindowIcon(app_icon())
    # Qt-eigene Texte (Standardknöpfe, Dateidialog) auf Deutsch
    translator = QTranslator(app)
    if translator.load(QLocale(QLocale.Language.German), "qtbase", "_", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(translator)
    if "windows11" in [k.lower() for k in QStyleFactory.keys()]:
        app.setStyle("windows11")
    else:
        app.setStyle("Fusion")

    window = MainWindow()
    window.show()
    if args.mysql:
        if not args.items:
            QMessageBox.critical(window, "Keine Datenbank", "Bitte mindestens eine Datenbank angeben.\n" + USAGE_HINT)
        else:
            window.load_mysql(args.mysql, args.items)
    elif args.items:
        window.open_file(args.items[0])
    else:
        QTimer.singleShot(0, window.open_connection_dialog)  # ohne Argumente gleich zum Einlesen
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
