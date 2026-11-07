"""Diagrammszene: hält die Items zu einem neutralen Modell und führt die Linien."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from PySide6.QtCore import QPointF, QRectF, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsScene

from erd_editor.analysis import ZERO_OR_MANY, derive_relationships
from erd_editor.diagram.container_item import ContainerItem
from erd_editor.diagram.entity_item import EntityItem
from erd_editor.diagram.palette import colors_for
from erd_editor.diagram.relation_item import RelationItem
from erd_editor.diagram.routing import Side, choose_sides, place, route_avoiding
from erd_editor.diagram.style import EntityStyle
from erd_editor.layout import LayoutNode, LayoutOptions, layered_layout
from erd_editor.model import Diagram, Layout, TableRef
from erd_editor.notation import CrowsFootNotation, Notation


class DiagramScene(QGraphicsScene):
    """Szene mit einem Behälter pro Datenbank, einer Box pro Tabelle und einer Linie pro Beziehung."""

    PARALLEL_OFFSET = 12.0
    NEW_TABLE_GAP = 60.0  # Abstand neuer Tabellen unter den vorhandenen ihrer Datenbank

    layout_changed = Signal()  # Nutzer hat Positionen verändert

    def __init__(self, parent=None, notation: Notation | None = None) -> None:
        super().__init__(parent)
        self.setBackgroundBrush(QColor("#F7F8FA"))
        self.notation = notation or CrowsFootNotation()
        self.entities: dict[TableRef, EntityItem] = {}
        self.relations: list[RelationItem] = []
        self.containers: dict[str, ContainerItem] = {}
        self.diagram: Diagram | None = None
        self._routing_suspended = False
        self.selectionChanged.connect(self._on_selection_changed)

    # ------------------------------------------------------------------ Aufbau

    def load(self, diagram: Diagram, layout: Layout | None = None, style: EntityStyle | None = None) -> None:
        """Szene leeren und alle Tabellen und Beziehungen des Diagramms anlegen.

        Ohne `layout` wird automatisch angeordnet. Mit `layout` behalten
        bekannte Tabellen ihre Position; neue Tabellen kommen unter die
        vorhandenen ihrer Datenbank, neue Datenbanken unter das Diagramm.
        """
        self.relations = []
        self.entities = {}
        self.containers = {}
        self.clear()
        self.diagram = diagram
        base_style = style or EntityStyle()
        colors_by_db = self._assign_colors(diagram, layout.database_colors if layout else {})
        for database in diagram.databases:
            color_index = colors_by_db[database.name]
            colors = colors_for(color_index)
            container = ContainerItem(database, colors, color_index)
            self.addItem(container)
            self.containers[database.name] = container
            # Entitäten übernehmen den Farbton ihrer Datenbank
            db_style = replace(base_style, header=colors.qcolor("entity_header"), border=colors.qcolor("border"))
            for schema in database.schemas:
                for table in schema.tables:
                    ref = TableRef(database.name, schema.name, table.name)
                    item = EntityItem(ref, table, db_style)
                    self.addItem(item)
                    self.entities[ref] = item
                    container.members.append(item)
        for rel in derive_relationships(diagram):
            item = RelationItem(rel, self.entities[rel.child], self.entities[rel.parent], self.notation)
            self.addItem(item)
            self.relations.append(item)
        if layout is None:
            self.auto_layout(notify=False)
        else:
            self._apply_layout(layout)

    @staticmethod
    def _assign_colors(diagram: Diagram, saved: dict[str, int]) -> dict[str, int]:
        """Gespeicherte Farben übernehmen, neue Datenbanken bekommen freie Farben."""
        result = {db.name: saved[db.name] for db in diagram.databases if db.name in saved}
        next_index = 0
        for db in diagram.databases:
            if db.name in result:
                continue
            while next_index in result.values():
                next_index += 1
            result[db.name] = next_index
            next_index += 1
        return result

    def current_layout(self) -> Layout:
        """Aktuelle Positionen und Farben (zum Speichern)."""
        return Layout(
            positions={ref: (item.pos().x(), item.pos().y()) for ref, item in self.entities.items()},
            database_colors={name: c.color_index for name, c in self.containers.items()},
            notation="crowsfoot",
        )

    def auto_layout(self, notify: bool = True) -> None:
        """Alle Boxen automatisch anordnen (Schichten-Layout je Datenbank)."""
        nodes = [
            LayoutNode(ref, item.rect.width(), item.rect.height(), ref.database)
            for ref, item in self.entities.items()
        ]
        edges = [(r.relationship.child, r.relationship.parent) for r in self.relations]
        self._set_positions(layered_layout(nodes, edges))
        if notify:
            self.layout_changed.emit()

    def _set_positions(self, positions: dict[TableRef, tuple[float, float]]) -> None:
        self._routing_suspended = True
        try:
            for ref, (x, y) in positions.items():
                self.entities[ref].setPos(x, y)
        finally:
            self._routing_suspended = False
        self.update_containers()
        self.update_relations()

    def _apply_layout(self, layout: Layout) -> None:
        """Gespeichertes Layout anwenden und neue Tabellen sinnvoll platzieren."""
        options = LayoutOptions()
        positions = {ref: pos for ref, pos in layout.positions.items() if ref in self.entities}
        new_by_db: dict[str, list[TableRef]] = defaultdict(list)
        for ref in self.entities:
            if ref not in positions:
                new_by_db[ref.database].append(ref)

        def bottom_of(refs) -> float:
            return max(positions[r][1] + self.entities[r].rect.height() for r in refs)

        new_databases = []
        for db_name, new_refs in new_by_db.items():
            known = [r for r in positions if r.database == db_name]
            if not known:
                new_databases.append(db_name)
                continue
            # Neue Tabellen in Zeilen unter die vorhandenen ihrer Datenbank
            left = min(positions[r][0] for r in known)
            right = max(positions[r][0] + self.entities[r].rect.width() for r in known)
            max_width = max(right - left, 1000.0)
            x, y, row_h = left, bottom_of(known) + self.NEW_TABLE_GAP, 0.0
            for ref in new_refs:
                rect = self.entities[ref].rect
                if x > left and x + rect.width() > left + max_width:
                    x, y, row_h = left, y + row_h + options.row_gap, 0.0
                positions[ref] = (x, y)
                x += rect.width() + options.row_gap * 1.5
                row_h = max(row_h, rect.height())

        # Neue Datenbanken automatisch anordnen und unter das Diagramm setzen
        edges = [(r.relationship.child, r.relationship.parent) for r in self.relations]
        for db_name in new_databases:
            refs = new_by_db[db_name]
            nodes = [LayoutNode(r, self.entities[r].rect.width(), self.entities[r].rect.height(), db_name) for r in refs]
            offset_y = bottom_of(positions) + options.group_gap if positions else 0.0
            for ref, (x, y) in layered_layout(nodes, edges).items():
                positions[ref] = (x, y + offset_y)

        self._set_positions(positions)

    # ------------------------------------------------------------------ Behälter

    def container_of(self, item: EntityItem) -> ContainerItem | None:
        return self.containers.get(item.ref.database)

    def update_containers(self) -> None:
        for container in self.containers.values():
            container.update_geometry()

    def move_container(self, container: ContainerItem, dx: float, dy: float) -> None:
        """Behälter samt Entitäten verschieben; Linien nur einmal neu führen."""
        self._routing_suspended = True
        try:
            for item in container.members:
                item.moveBy(dx, dy)
        finally:
            self._routing_suspended = False
        container.update_geometry()
        self.update_relations()
        self.layout_changed.emit()

    # ------------------------------------------------------------------ Linienführung

    def entity_moved(self, item: EntityItem) -> None:
        """Wird von EntityItem bei jeder Positionsänderung aufgerufen."""
        if self._routing_suspended:
            return
        container = self.container_of(item)
        if container is not None:
            container.update_geometry()  # Rahmen wächst mit
        if self.relations:
            self.update_relations()
        self.layout_changed.emit()

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
        all_rects = {id(item): scene_rect(item) for item in self.entities.values()}
        for i, rel_item in enumerate(self.relations):
            rel = rel_item.relationship
            child_side, parent_side = sides[i]
            if child_side is parent_side:
                offset = rel.parallel_index * self.PARALLEL_OFFSET
            else:
                offset = (rel.parallel_index - (rel.parallel_count - 1) / 2) * self.PARALLEL_OFFSET
            skip = {id(rel_item.child_item), id(rel_item.parent_item)}
            obstacles = [r for key, r in all_rects.items() if key not in skip]
            points = route_avoiding(starts[i], child_side, ends[i], parent_side, stub, offset, obstacles)
            rel_item.set_route(points, child_side, parent_side)

    def _on_selection_changed(self) -> None:
        for rel_item in self.relations:
            rel_item.update()
