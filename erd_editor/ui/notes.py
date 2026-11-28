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
        menu.addSeparator()
        self._add_connect_menu(menu, note_id, item.entity)
        if item.entity is not None:
            menu.addAction(f"Verbindung zu „{item.entity.table}“ lösen").triggered.connect(
                lambda: self.detach_note(note_id))
        menu.addSeparator()
        menu.addAction("Notiz löschen").triggered.connect(lambda: self.delete_notes([note_id]))
        return menu

    def _add_connect_menu(self, menu: QMenu, note_id: str, current: TableRef | None) -> None:
        """Untermenü „Verbinden mit“: alle Tabellen und Views der Ansicht, die markierte zuerst."""
        sub = menu.addMenu("Verbinden mit")
        refs = sorted(self.scene.entities, key=lambda r: (r.table.casefold(), r.key.casefold()))
        if not refs:
            sub.addAction("(keine Tabellen in dieser Ansicht)").setEnabled(False)
            return
        names = [r.table.casefold() for r in refs]
        selected = self.selected_refs()
        first = selected[0] if len(selected) == 1 and selected[0] in self.scene.entities else None

        def add(ref: TableRef, prefix: str = "") -> None:
            # gleicher Tabellenname in mehreren Datenbanken/Schemas → voller Name
            label = ref.key if names.count(ref.table.casefold()) > 1 else ref.table
            action = sub.addAction(prefix + label)
            action.setCheckable(True)
            action.setChecked(ref == current)
            if ref != current:
                action.triggered.connect(lambda _checked=False, r=ref: self.attach_note(note_id, r))
            else:
                action.triggered.connect(lambda _checked=False: action.setChecked(True))

        if first is not None:
            add(first, "Markiert: ")
            sub.addSeparator()
        for ref in refs:
            add(ref)

    @staticmethod
    def note_label(text: str, limit: int = 40) -> str:
        """Erste Zeile des Notiztexts, gekürzt – für Menüs."""
        line = text.strip().splitlines()[0] if text.strip() else "(leer)"
        return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"

    def entity_menu(self, ref: TableRef, screen_pos) -> None:
        self.build_entity_menu(ref).exec(screen_pos)

    def build_entity_menu(self, ref: TableRef) -> QMenu:
        menu = QMenu(self)
        noun = "View" if (t := self.document.diagram.find_table(ref)) is not None and t.is_view else "Tabelle"
        menu.addAction(f"{noun} bearbeiten …").triggered.connect(lambda: self.edit_table(ref))
        menu.addAction("Notiz anheften …").triggered.connect(lambda: self.add_note(entity=ref))
        self._add_note_menu(menu, ref)
        return menu

    def _add_note_menu(self, menu: QMenu, ref: TableRef) -> None:
        """Untermenü „Notiz verbinden“: vorhandene Notizen der Ansicht, die markierte zuerst."""
        sub = menu.addMenu("Notiz verbinden")
        notes = sorted(self.scene.notes.values(), key=lambda item: item.text.casefold())
        if not notes:
            sub.addAction("(keine Notizen in dieser Ansicht)").setEnabled(False)
            return
        selected = self.selected_note_ids()
        first = selected[0] if len(selected) == 1 else None

        def add(item, prefix: str = "") -> None:
            label = self.note_label(item.text)
            if item.entity is not None and item.entity != ref:
                label += f"  (bisher: {item.entity.table})"
            action = sub.addAction(prefix + label)
            action.setToolTip(item.text)
            action.setCheckable(True)
            action.setChecked(item.entity == ref)
            if item.entity != ref:
                action.triggered.connect(lambda _checked=False, nid=item.id: self.attach_note(nid, ref))
            else:
                action.triggered.connect(lambda _checked=False: action.setChecked(True))

        if first is not None:
            add(self.scene.notes[first], "Markiert: ")
            sub.addSeparator()
        for item in notes:
            add(item)
        sub.setToolTipsVisible(True)

    def canvas_menu(self, scene_pos: QPointF, screen_pos) -> None:
        self.build_canvas_menu(scene_pos).exec(screen_pos)

    def build_canvas_menu(self, scene_pos: QPointF) -> QMenu:
        menu = QMenu(self)
        menu.addAction("Notiz hier einfügen …").triggered.connect(
            lambda: self.add_note(at=QPointF(scene_pos), use_selection=False))
        return menu
