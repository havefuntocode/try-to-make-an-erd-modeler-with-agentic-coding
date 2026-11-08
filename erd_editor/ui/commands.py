"""Befehle für Rückgängig/Wiederholen (`QUndoStack`).

Jeder Befehl speichert den Zustand vorher und nachher und stellt ihn
wieder her – das ist robust und braucht wenig Speicher, weil ein Layout
nur aus Positionen, Rahmen und Optionen besteht.

- `LayoutCommand`: alles, was nur das Layout ändert (Verschieben,
  Rahmen, Anordnen, Darstellungsgröße, Darstellungsoptionen).
- `DocumentCommand`: Änderungen am Schema (Neu einlesen, Datenbanken
  hinzufügen); stellt Diagramm, Verbindungen und Layout wieder her.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QUndoCommand

from erd_editor.diagram.scene import DiagramScene
from erd_editor.model import Layout


class LayoutCommand(QUndoCommand):
    """Layout-Änderung, die bereits ausgeführt ist, wenn der Befehl auf den Stapel kommt."""

    def __init__(self, scene: DiagramScene, before: Layout, after: Layout, text: str,
                 on_restore: Callable[[], None] | None = None,
                 prepare: Callable[[], None] | None = None) -> None:
        super().__init__(text)
        self.scene = scene
        self.before = before
        self.after = after
        self.on_restore = on_restore  # z. B. Menüs nachziehen
        self.prepare = prepare  # z. B. in die Ansicht wechseln, in der die Änderung passiert ist
        self._done = True  # beim Ablegen auf dem Stapel nicht erneut ausführen

    def redo(self) -> None:
        if self._done:
            self._done = False
            return
        self._apply(self.after)

    def undo(self) -> None:
        self._apply(self.before)

    def _apply(self, layout: Layout) -> None:
        if self.prepare:
            self.prepare()
        self.scene.restore_layout(layout)
        if self.on_restore:
            self.on_restore()


class DocumentCommand(QUndoCommand):
    """Änderung am Dokument (Schema, Layouts, Teildiagramme), bereits ausgeführt beim Ablegen.

    `before`/`after` sind Schnappschüsse, die `restore` versteht.
    """

    def __init__(self, restore: Callable[[object], None], before: object, after: object, text: str) -> None:
        super().__init__(text)
        self.restore = restore
        self.before = before
        self.after = after
        self._done = True

    def redo(self) -> None:
        if self._done:
            self._done = False
            return
        self.restore(self.after)

    def undo(self) -> None:
        self.restore(self.before)
