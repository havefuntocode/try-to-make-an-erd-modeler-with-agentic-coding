"""Diagrammszene: hält die Items zu einem neutralen Modell."""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsScene

from erd_editor.diagram.entity_item import EntityItem
from erd_editor.diagram.style import EntityStyle
from erd_editor.model import Diagram, TableRef


class DiagramScene(QGraphicsScene):
    """Szene mit einer Entitätsbox pro Tabelle.

    Die Anordnung ist vorläufig ein einfaches Raster; das Auto-Layout
    (`erd_editor.layout`) ersetzt sie in einem späteren Schritt.
    """

    GRID_COLUMNS = 4
    SPACING = 40.0
    DATABASE_GAP = 80.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setBackgroundBrush(QColor("#F7F8FA"))
        self.entities: dict[TableRef, EntityItem] = {}
        self.diagram: Diagram | None = None

    def load(self, diagram: Diagram, style: EntityStyle | None = None) -> None:
        """Szene leeren und alle Tabellen des Diagramms als Boxen anlegen."""
        self.clear()
        self.entities.clear()
        self.diagram = diagram
        style = style or EntityStyle()
        for ref, table in diagram.tables():
            item = EntityItem(ref, table, style)
            self.addItem(item)
            self.entities[ref] = item
        self._arrange_grid()

    def _arrange_grid(self) -> None:
        """Pro Datenbank ein Raster, Datenbanken untereinander."""
        y_db = 0.0
        databases: dict[str, list[EntityItem]] = {}
        for ref, item in self.entities.items():
            databases.setdefault(ref.database, []).append(item)
        for items in databases.values():
            y_row = y_db
            for start in range(0, len(items), self.GRID_COLUMNS):
                row = items[start : start + self.GRID_COLUMNS]
                x = 0.0
                for item in row:
                    item.setPos(x, y_row)
                    x += item.rect.width() + self.SPACING
                y_row += max(i.rect.height() for i in row) + self.SPACING
            y_db = y_row + self.DATABASE_GAP - self.SPACING
