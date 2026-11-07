"""Diagrammszene: hält die Items zu einem neutralen Modell und führt die Linien."""

from __future__ import annotations

from collections import defaultdict

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsScene

from erd_editor.analysis import ZERO_OR_MANY, derive_relationships
from erd_editor.diagram.entity_item import EntityItem
from erd_editor.diagram.relation_item import RelationItem
from erd_editor.diagram.routing import Side, choose_sides, place, route
from erd_editor.diagram.style import EntityStyle
from erd_editor.model import Diagram, TableRef
from erd_editor.notation import CrowsFootNotation, Notation


class DiagramScene(QGraphicsScene):
    """Szene mit einer Entitätsbox pro Tabelle und einer Linie pro Beziehung.

    Die Anordnung ist vorläufig ein einfaches Raster; das Auto-Layout
    (`erd_editor.layout`) ersetzt sie in einem späteren Schritt.
    """

    GRID_COLUMNS = 4
    SPACING_X = 90.0
    SPACING_Y = 50.0
    DATABASE_GAP = 100.0
    PARALLEL_OFFSET = 12.0

    def __init__(self, parent=None, notation: Notation | None = None) -> None:
        super().__init__(parent)
        self.setBackgroundBrush(QColor("#F7F8FA"))
        self.notation = notation or CrowsFootNotation()
        self.entities: dict[TableRef, EntityItem] = {}
        self.relations: list[RelationItem] = []
        self.diagram: Diagram | None = None
        self.selectionChanged.connect(self._on_selection_changed)

    # ------------------------------------------------------------------ Aufbau

    def load(self, diagram: Diagram, style: EntityStyle | None = None) -> None:
        """Szene leeren und alle Tabellen und Beziehungen des Diagramms anlegen."""
        self.relations = []
        self.entities = {}
        self.clear()
        self.diagram = diagram
        style = style or EntityStyle()
        for ref, table in diagram.tables():
            item = EntityItem(ref, table, style)
            self.addItem(item)
            self.entities[ref] = item
        self._arrange_grid()

        for rel in derive_relationships(diagram):
            item = RelationItem(rel, self.entities[rel.child], self.entities[rel.parent], self.notation)
            self.addItem(item)
            self.relations.append(item)
        self.update_relations()

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
                    x += item.rect.width() + self.SPACING_X
                y_row += max(i.rect.height() for i in row) + self.SPACING_Y
            y_db = y_row + self.DATABASE_GAP - self.SPACING_Y

    # ------------------------------------------------------------------ Linienführung

    def entity_moved(self, item: EntityItem) -> None:
        """Wird von EntityItem bei jeder Positionsänderung aufgerufen."""
        if self.relations:
            self.update_relations()

    def update_relations(self) -> None:
        """Alle Linien neu führen.

        Schritt 1: Seiten und Kind-Andockpunkte bestimmen.
        Schritt 2: Eltern-Andockpunkte je Box und Seite verteilen.
        Schritt 3: Linien führen.
        """
        if not self.relations:
            return
        stub = self.notation.end_length(ZERO_OR_MANY) + 8
        spacing = 2 * self.notation.end_width() + 4

        def scene_rect(item: EntityItem) -> QRectF:
            return item.mapRectToScene(item.rect)

        def column_y(item: EntityItem, column: str) -> float | None:
            y = item.column_anchor_y(column)
            return None if y is None else item.mapToScene(QPointF(0, y)).y()

        # Schritt 1
        sides: list[tuple[Side, Side]] = []
        starts: list[QPointF] = []
        desired: list[float] = []
        for rel_item in self.relations:
            child, parent = rel_item.child_item, rel_item.parent_item
            fk = rel_item.relationship.foreign_key
            c_rect, p_rect = scene_rect(child), scene_rect(parent)
            child_side, parent_side = choose_sides(c_rect, p_rect, 2 * stub, same_item=child is parent)
            start_y = column_y(child, fk.columns[0])
            if start_y is None:
                start_y = c_rect.center().y()
            starts.append(QPointF(child_side.x_of(c_rect), start_y))
            sides.append((child_side, parent_side))
            want = start_y
            if child is parent:  # Selbstreferenz: an der referenzierten Spalte andocken
                ref_y = column_y(parent, fk.referenced_columns[0])
                want = ref_y if ref_y is not None and abs(ref_y - start_y) >= spacing else c_rect.top()
            desired.append(want)

        # Schritt 2
        groups: dict[tuple[int, Side], list[int]] = defaultdict(list)
        for i, rel_item in enumerate(self.relations):
            groups[(id(rel_item.parent_item), sides[i][1])].append(i)
        child_ends: dict[tuple[int, Side], list[float]] = defaultdict(list)
        for i, rel_item in enumerate(self.relations):
            child_ends[(id(rel_item.child_item), sides[i][0])].append(starts[i].y())

        ends: list[QPointF] = [QPointF()] * len(self.relations)
        for key, indices in groups.items():
            parent = self.relations[indices[0]].parent_item
            p_rect = scene_rect(parent)
            margin = parent.style.radius + 4
            ys = place(
                [desired[i] for i in indices],
                child_ends.get(key, []),
                p_rect.top() + margin,
                p_rect.bottom() - margin,
                spacing,
            )
            for i, y in zip(indices, ys):
                ends[i] = QPointF(key[1].x_of(p_rect), y)

        # Schritt 3
        for i, rel_item in enumerate(self.relations):
            rel = rel_item.relationship
            child_side, parent_side = sides[i]
            if child_side is parent_side:
                offset = rel.parallel_index * self.PARALLEL_OFFSET
            else:
                offset = (rel.parallel_index - (rel.parallel_count - 1) / 2) * self.PARALLEL_OFFSET
            points = route(starts[i], child_side, ends[i], parent_side, stub, offset)
            rel_item.set_route(points, child_side, parent_side)

    def _on_selection_changed(self) -> None:
        for rel_item in self.relations:
            rel_item.update()
