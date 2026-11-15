"""Notizen (Infoboxen) im Hauptfenster: anlegen, bearbeiten, verbinden, löschen.

Als Mixin für `MainWindow`. Notizen gehören zum Layout der aktuellen
Ansicht; jede Änderung läuft über `record_layout()` und ist damit ein
Rückgängig-Schritt. Erwartet: `scene`, `view`, `record_layout`,
`statusBar()`, `selected_refs()`, `edit_table()`.
"""

from __future__ import annotations

import uuid
from dataclasses import replace

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QInputDialog, QMenu

from erd_editor.model import Note, TableRef


class NotesMixin:
    # ------------------------------------------------------------------ Hilfen

    def _notes(self) -> list[Note]:
        return [item.note for item in self.scene.notes.values()]

    def _set_notes(self, label: str, notes: list[Note]) -> None:
        """Notizen setzen – `label` ist der Text im Menü „Rückgängig: …“."""
        self.record_layout(label, lambda: self.scene.apply_notes(notes))

    def _replace_note(self, note_id: str, label: str, **changes) -> None:
        self._set_notes(label, [replace(n, **changes) if n.id == note_id else n for n in self._notes()])

    def ask_note_text(self, current: str = "") -> str | None:
        """Text eingeben (in Tests austauschbar); None = abgebrochen."""
        text, ok = QInputDialog.getMultiLineText(self, "Notiz", "Text der Notiz:", current)
        return text.strip() if ok else None

    def selected_note_ids(self) -> list[str]:
        return [note_id for note_id, item in self.scene.notes.items() if item.isSelected()]

    # ------------------------------------------------------------------ Aktionen

    def add_note(self, entity: TableRef | None = None, at: QPointF | None = None, text: str | None = None,
                 use_selection: bool = True) -> str | None:
        """Neue Notiz; mit genau einer markierten Tabelle wird sie mit ihr verbunden.

        Ohne Position kommt sie neben die Tabelle bzw. in die Mitte des
        sichtbaren Bereichs. Liefert die ID der neuen Notiz.
        """
        if entity is None and use_selection:
            refs = self.selected_refs()
            entity = refs[0] if len(refs) == 1 else None
        if text is None:
            text = self.ask_note_text()
            if text is None:
                return None
        if at is None and entity is None:
            at = self.view.mapToScene(self.view.viewport().rect().center())
        note = Note(uuid.uuid4().hex[:12], text, self.scene.new_note_frame(entity, at), entity)
        self._set_notes("Notiz hinzufügen", self._notes() + [note])
        self.scene.clearSelection()
        self.scene.notes[note.id].setSelected(True)
        self.statusBar().showMessage("Notiz hinzugefügt" + (f" – verbunden mit „{entity.table}“" if entity else ""))
        return note.id

    def edit_note(self, note_id: str) -> None:
        item = self.scene.notes.get(note_id)
        if item is None:
            return
        text = self.ask_note_text(item.text)
        if text is not None and text != item.text:
            self._replace_note(note_id, "Notiz bearbeiten", text=text)

    def attach_note(self, note_id: str, entity: TableRef) -> None:
        self._replace_note(note_id, f"Notiz mit „{entity.table}“ verbinden", entity=entity)

    def detach_note(self, note_id: str) -> None:
        self._replace_note(note_id, "Notiz-Verbindung lösen", entity=None)

    def delete_notes(self, note_ids: list[str]) -> None:
        if not note_ids:
            return
        doomed = set(note_ids)
        text = "Notiz löschen" if len(doomed) == 1 else f"{len(doomed)} Notizen löschen"
        self._set_notes(text, [n for n in self._notes() if n.id not in doomed])
        self.statusBar().showMessage(text.replace("löschen", "gelöscht"))

    def delete_selection(self) -> None:
        """Entf: markierte Notizen löschen, dann ggf. markierte Tabellen (mit Rückfrage)."""
        notes = self.selected_note_ids()
        self.delete_notes(notes)
        if self.selected_refs():
            self.delete_selected_tables()
        elif not notes:
            self.statusBar().showMessage("Nichts markiert – bitte Tabellen oder Notizen markieren")

    # ------------------------------------------------------------------ Kontextmenüs

    def note_menu(self, note_id: str, screen_pos) -> None:
        self.build_note_menu(note_id).exec(screen_pos)

    def build_note_menu(self, note_id: str) -> QMenu:
        item = self.scene.notes[note_id]
        menu = QMenu(self)
        menu.addAction("Notiz bearbeiten …").triggered.connect(lambda: self.edit_note(note_id))
        refs = self.selected_refs()
        if item.entity is not None:
            menu.addAction(f"Verbindung zu „{item.entity.table}“ lösen").triggered.connect(
                lambda: self.detach_note(note_id))
        if len(refs) == 1 and refs[0] != item.entity:
            menu.addAction(f"Mit „{refs[0].table}“ verbinden").triggered.connect(
                lambda: self.attach_note(note_id, refs[0]))
        menu.addSeparator()
        menu.addAction("Notiz löschen").triggered.connect(lambda: self.delete_notes([note_id]))
        return menu

    def entity_menu(self, ref: TableRef, screen_pos) -> None:
        self.build_entity_menu(ref).exec(screen_pos)

    def build_entity_menu(self, ref: TableRef) -> QMenu:
        menu = QMenu(self)
        noun = "View" if (t := self.document.diagram.find_table(ref)) is not None and t.is_view else "Tabelle"
        menu.addAction(f"{noun} bearbeiten …").triggered.connect(lambda: self.edit_table(ref))
        menu.addAction("Notiz anheften …").triggered.connect(lambda: self.add_note(entity=ref))
        selected = self.selected_note_ids()
        if len(selected) == 1:
            menu.addAction("Markierte Notiz hiermit verbinden").triggered.connect(
                lambda: self.attach_note(selected[0], ref))
        return menu

    def canvas_menu(self, scene_pos: QPointF, screen_pos) -> None:
        self.build_canvas_menu(scene_pos).exec(screen_pos)

    def build_canvas_menu(self, scene_pos: QPointF) -> QMenu:
        menu = QMenu(self)
        menu.addAction("Notiz hier einfügen …").triggered.connect(
            lambda: self.add_note(at=QPointF(scene_pos), use_selection=False))
        return menu
