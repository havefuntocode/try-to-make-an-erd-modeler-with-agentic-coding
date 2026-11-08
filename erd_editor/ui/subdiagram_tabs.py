"""Teildiagramme im Hauptfenster: Reiter, Ansichtswechsel, Anlegen/Bearbeiten/Löschen.

Als Mixin für `MainWindow` ausgelagert, damit das Hauptfenster
überschaubar bleibt. Erwartet dort: `scene`, `view`, `document`,
`undo_stack`, `statusBar()`, `_sync_menus()`, `setWindowModified()`.

Ansicht `None` = „Gesamt“ (alle Tabellen, Layout in `document.layout`);
sonst der Name eines Teildiagramms (Layout im `Subdiagram`).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtWidgets import QMenu, QMessageBox, QTabBar, QVBoxLayout, QWidget

from erd_editor.diagram.scene import DiagramScene
from erd_editor.model import Diagram, Layout, Subdiagram, TableRef, filter_diagram
from erd_editor.model.subdiagram import existing_tables, unique_name
from erd_editor.persistence.document import DiagramDocument
from erd_editor.ui.subdiagram_dialog import SubdiagramDialog

MAIN_VIEW_TITLE = "Gesamt"


@dataclass(frozen=True)
class Snapshot:
    """Zustand für Rückgängig bei Schema-/Teildiagramm-Änderungen."""

    document: DiagramDocument
    view: str | None


class SubdiagramTabsMixin:
    current_view: str | None = None

    # ------------------------------------------------------------------ Aufbau

    def _init_tabs(self) -> None:
        self.tabs = QTabBar()
        self.tabs.setDocumentMode(True)
        self.tabs.setExpanding(False)
        self.tabs.setMovable(False)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.tabBarDoubleClicked.connect(self._tab_double_clicked)
        self.tabs.setContextMenuPolicy(self.tabs.contextMenuPolicy().CustomContextMenu)
        self.tabs.customContextMenuRequested.connect(self._tab_context_menu)
        self._syncing_tabs = False
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.tabs)
        layout.addWidget(self.view, 1)
        self.setCentralWidget(central)
        self.sync_tabs()

    def sync_tabs(self) -> None:
        """Reiter an die Teildiagramme des Dokuments anpassen."""
        self._syncing_tabs = True
        try:
            while self.tabs.count():
                self.tabs.removeTab(0)
            self.tabs.addTab(MAIN_VIEW_TITLE)
            for sd in self.document.subdiagrams:
                self.tabs.addTab(sd.name)
            names = [None] + [sd.name for sd in self.document.subdiagrams]
            self.tabs.setCurrentIndex(names.index(self.current_view) if self.current_view in names else 0)
            self.tabs.setVisible(bool(self.document.subdiagrams))
        finally:
            self._syncing_tabs = False

    # ------------------------------------------------------------------ Ansichten

    def view_names(self) -> list[str | None]:
        return [None] + [sd.name for sd in self.document.subdiagrams]

    def main_colors(self) -> dict[str, int]:
        """Datenbankfarben der Gesamtansicht – gelten in allen Teildiagrammen."""
        layout = self.scene.current_layout() if self.current_view is None and self.scene.entities else self.document.layout
        return DiagramScene._assign_colors(self.document.diagram, layout.database_colors)

    def store_view_state(self) -> None:
        """Aktuelles Layout der Szene in die aktuelle Ansicht übernehmen."""
        if not self.scene.entities and not self.document.diagram.databases:
            return
        layout = self.scene.current_layout()
        if self.current_view is None:
            self.document.layout = layout
        else:
            sd = self.document.subdiagram(self.current_view)
            if sd is not None:
                sd.layout = layout

    def show_view(self, name: str | None) -> None:
        """Ansicht anzeigen, ohne den bisherigen Zustand zu speichern."""
        if name is not None and self.document.subdiagram(name) is None:
            name = None
        self.current_view = name
        if name is None:
            # ohne Positionen ordnet die Szene automatisch an; Größe und Optionen gelten trotzdem
            self.scene.load(self.document.diagram, self.document.layout)
            self.document.layout = self.scene.current_layout()
        else:
            sd = self.document.subdiagram(name)
            colors = self.main_colors()
            if sd.layout is None:  # erstes Anzeigen: automatisch, aber mit Größe/Optionen von „Gesamt“
                main = self.document.layout
                start = Layout(database_colors=colors, scale=main.scale, display=main.display)
            else:
                start = replace(sd.layout, database_colors=colors)
            self.scene.load(filter_diagram(self.document.diagram, sd.tables), start)
            sd.layout = self.scene.current_layout()
        self.sync_tabs()
        self._sync_menus()
        QTimer.singleShot(0, self.view.zoom_to_fit)

    def activate_view(self, name: str | None) -> None:
        """In eine Ansicht wechseln (bisherigen Zustand vorher sichern)."""
        if name == self.current_view:
            return
        self.store_view_state()
        self.show_view(name)

    def _tab_changed(self, index: int) -> None:
        if self._syncing_tabs or index < 0:
            return
        names = self.view_names()
        if index < len(names):
            self.activate_view(names[index])

    def _tab_double_clicked(self, index: int) -> None:
        if index > 0:
            self.edit_subdiagram(self.view_names()[index])

    def _tab_context_menu(self, pos: QPoint) -> None:
        index = self.tabs.tabAt(pos)
        menu = QMenu(self)
        menu.addAction("Neues Teildiagramm …", self.new_subdiagram)
        if index > 0:
            name = self.view_names()[index]
            menu.addSeparator()
            menu.addAction("Bearbeiten …", lambda: self.edit_subdiagram(name))
            menu.addAction("Löschen", lambda: self.delete_subdiagram(name))
        menu.exec(self.tabs.mapToGlobal(pos))

    def step_view(self, direction: int) -> None:
        names = self.view_names()
        index = (names.index(self.current_view) + direction) % len(names)
        self.activate_view(names[index])

    # ------------------------------------------------------------------ Rückgängig

    def snapshot(self) -> Snapshot:
        """Dokument samt Teildiagrammen und aktueller Ansicht (flache Kopie)."""
        self.store_view_state()
        doc = self.document
        copy = DiagramDocument(
            doc.diagram,
            doc.layout,
            list(doc.connections),
            [Subdiagram(sd.name, list(sd.tables), sd.layout) for sd in doc.subdiagrams],
        )
        return Snapshot(copy, self.current_view)

    def restore_snapshot(self, snap: Snapshot) -> None:
        doc = snap.document
        self.document = DiagramDocument(
            doc.diagram,
            doc.layout,
            list(doc.connections),
            [Subdiagram(sd.name, list(sd.tables), sd.layout) for sd in doc.subdiagrams],
        )
        self.show_view(snap.view)

    def record_document(self, before: Snapshot, text: str) -> None:
        from erd_editor.ui.commands import DocumentCommand

        self.undo_stack.push(DocumentCommand(self.restore_snapshot, before, self.snapshot(), text))

    # ------------------------------------------------------------------ Anlegen, Bearbeiten, Löschen

    def taken_names(self) -> set[str]:
        return {sd.name for sd in self.document.subdiagrams}

    def selected_refs(self) -> list[TableRef]:
        return [item.ref for item in self.scene.selectedItems() if hasattr(item, "ref")]

    def new_subdiagram(self, from_selection: bool = False) -> None:
        """Teildiagramm → Neu … (vorbelegt mit den markierten Tabellen)."""
        if not self.document.diagram.databases:
            self.statusBar().showMessage("Zuerst eine Datenbank einlesen oder ein Diagramm öffnen")
            return
        selected = self.selected_refs()
        if from_selection and not selected:
            self.statusBar().showMessage("Bitte zuerst Tabellen markieren (Klick, Strg+Klick oder Rahmen ziehen)")
            return
        name = unique_name("Teildiagramm", self.taken_names() | {MAIN_VIEW_TITLE})
        if from_selection:
            self.add_subdiagram(name, selected)
            return
        dialog = SubdiagramDialog(self.document.diagram, name, selected, self.taken_names(), parent=self)
        if dialog.exec() == SubdiagramDialog.DialogCode.Accepted:
            self.add_subdiagram(dialog.chosen_name(), dialog.selected_tables())

    def add_subdiagram(self, name: str, tables: list[TableRef]) -> None:
        before = self.snapshot()
        self.document.subdiagrams.append(Subdiagram(name, list(tables)))
        self.store_view_state()
        self.show_view(name)
        self.record_document(before, f"Teildiagramm „{name}“ anlegen")
        self.statusBar().showMessage(f"Teildiagramm „{name}“ mit {len(tables)} Tabellen angelegt")

    def edit_subdiagram(self, name: str | None = None) -> None:
        name = name if name is not None else self.current_view
        sd = self.document.subdiagram(name)
        if sd is None:
            self.statusBar().showMessage("„Gesamt“ zeigt immer alle Tabellen – bitte ein Teildiagramm wählen")
            return
        dialog = SubdiagramDialog(self.document.diagram, sd.name, sd.tables, self.taken_names(),
                                  title="Teildiagramm bearbeiten", parent=self)
        if dialog.exec() != SubdiagramDialog.DialogCode.Accepted:
            return
        new_name, tables = dialog.chosen_name(), dialog.selected_tables()
        if new_name == sd.name and set(tables) == set(sd.tables):
            return
        before = self.snapshot()
        sd = self.document.subdiagram(name)
        sd.name, sd.tables = new_name, list(tables)
        self.show_view(new_name)
        self.record_document(before, f"Teildiagramm „{new_name}“ bearbeiten")

    def delete_subdiagram(self, name: str | None = None) -> None:
        name = name if name is not None else self.current_view
        sd = self.document.subdiagram(name)
        if sd is None:
            self.statusBar().showMessage("„Gesamt“ kann nicht gelöscht werden")
            return
        answer = QMessageBox.question(self, "Teildiagramm löschen",
                                      f"Teildiagramm „{name}“ löschen?\nDie Tabellen bleiben im Diagramm erhalten.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        before = self.snapshot()
        self.document.subdiagrams = [s for s in self.document.subdiagrams if s.name != name]
        self.show_view(None)
        self.record_document(before, f"Teildiagramm „{name}“ löschen")

    def prune_subdiagrams(self, diagram: Diagram) -> None:
        """Nach dem Neu-Einlesen: verschwundene Tabellen aus den Teildiagrammen entfernen."""
        for sd in self.document.subdiagrams:
            sd.tables = existing_tables(diagram, sd.tables)
