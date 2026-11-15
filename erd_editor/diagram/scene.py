"""Diagrammszene: hält die Items zu einem neutralen Modell und führt die Linien.

Ebenen (z-Wert): Datenbank-Behälter (-2) < Schema-Rahmen (-1.5) < Linien (-1) < Entitäten (0).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from dataclasses import replace

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsScene, QGraphicsSceneContextMenuEvent, QGraphicsSceneMouseEvent

from erd_editor.analysis import ZERO_OR_MANY, RelationshipKey, RelationshipOverride, apply_overrides, derive_relationships
from erd_editor.diagram.container_item import ContainerItem
from erd_editor.diagram.dependency_item import DependencyItem
from erd_editor.diagram.entity_item import EntityItem
from erd_editor.diagram.note_item import NoteItem, NoteLinkItem
from erd_editor.diagram.palette import colors_for
from erd_editor.diagram.relation_item import RelationItem
from erd_editor.diagram.routing import Side, choose_sides, place, route_avoiding
from erd_editor.diagram.schema_item import SchemaItem
from erd_editor.diagram.style import EntityStyle
from erd_editor.layout import LayoutNode, LayoutOptions, layered_layout
from erd_editor.model import Diagram, DisplayOptions, Layout, Note, TableRef
from erd_editor.model.layout import MAX_SCALE, MIN_SCALE
from erd_editor.notation import CrowsFootNotation, Notation, notation_for

Position = tuple[float, float]


class DiagramScene(QGraphicsScene):
    """Szene mit Behältern (Datenbanken, Schemas), Boxen (Tabellen) und Linien (Beziehungen)."""

    PARALLEL_OFFSET = 12.0
    NEW_TABLE_GAP = 60.0  # Abstand neuer Tabellen unter den vorhandenen
    SCHEMA_GAP = 110.0  # Abstand zwischen Schema-Blöcken (Platz für Rahmen und Kopfzeile)
    SCHEMA_EXTRA_GAP = 70.0  # zusätzlicher Abstand zwischen Datenbanken mit Schema-Rahmen

    layout_changed = Signal()  # Nutzer hat Positionen verändert
    # Mausgeste abgeschlossen: Layout vorher, nachher, Beschreibung (für Rückgängig)
    gesture_finished = Signal(object, object, str)
    # Rechtsklick auf eine Linie: RelationItem, Bildschirmposition
    relation_context_requested = Signal(object, object)
    entity_activated = Signal(object)  # Doppelklick auf eine Box: TableRef
    note_activated = Signal(str)  # Doppelklick auf eine Notiz: ID
    # Rechtsklick: Notiz (ID), Box (TableRef) oder freie Fläche (Szenenposition) + Bildschirmposition
    note_context_requested = Signal(str, object)
    entity_context_requested = Signal(object, object)
    canvas_context_requested = Signal(object, object)

    def __init__(self, parent=None, notation: Notation | None = None) -> None:
        super().__init__(parent)
        self.setBackgroundBrush(QColor("#F7F8FA"))
        self.notation = notation or CrowsFootNotation()
        self.entities: dict[TableRef, EntityItem] = {}
        self.relations: list[RelationItem] = []
        self.dependencies: list[DependencyItem] = []  # View → benutzte Objekte
        self.notes: dict[str, NoteItem] = {}  # Notizen (Infoboxen), Reihenfolge wie im Layout
        self.note_links: dict[str, NoteLinkItem] = {}  # Notiz-ID → Linie zur Tabelle
        self.containers: dict[str, ContainerItem] = {}
        self.schema_items: dict[tuple[str, str], SchemaItem] = {}
        self.diagram: Diagram | None = None
        self._routing_suspended = False
        self.display_scale = 1.0  # Darstellungsgröße (1.0 = 100 %)
        self.display_options = DisplayOptions()
        self._gesture_before: Layout | None = None
        self.overrides: dict[RelationshipKey, RelationshipOverride] = {}
        self._base_style = EntityStyle()
        self.selectionChanged.connect(self._on_selection_changed)

    # ------------------------------------------------------------------ Aufbau

    def load(
        self,
        diagram: Diagram,
        layout: Layout | None = None,
        style: EntityStyle | None = None,
        overrides: dict[RelationshipKey, RelationshipOverride] | None = None,
    ) -> None:
        """Szene leeren und alle Tabellen und Beziehungen des Diagramms anlegen.

        Ohne `layout` wird automatisch angeordnet. Mit `layout` behalten
        bekannte Tabellen ihre Position; neue Tabellen kommen unter die
        vorhandenen ihres Schemas bzw. ihrer Datenbank, neue Datenbanken
        unter das Diagramm.
        """
        self.relations = []
        self.dependencies = []
        self.notes = {}
        self.note_links = {}
        self.entities = {}
        self.containers = {}
        self.schema_items = {}
        self.clear()
        self.diagram = diagram
        self.overrides = dict(overrides or {})
        if layout is not None:
            if layout.notation != self.notation.key:
                self.notation = notation_for(layout.notation)
            self.display_scale = min(max(layout.scale, MIN_SCALE), MAX_SCALE)
            self.display_options = layout.display
        self._base_style = style or EntityStyle()
        colors_by_db = self._assign_colors(diagram, layout.database_colors if layout else {})
        for database in diagram.databases:
            container = self._add_container(database, colors_by_db[database.name])
            for schema in database.schemas:
                if schema.name is not None:  # MySQL: keine Schemaebene
                    self._add_schema_item(container, database, schema)
                for table in schema.tables:
                    self._add_entity(TableRef(database.name, schema.name, table.name), table)
        self._build_relations()
        if layout is None:
            self.auto_layout(notify=False)
        else:
            self._apply_layout(layout)
            self._apply_frames(layout)
            self.apply_notes(layout.notes)

    def _add_container(self, database, color_index: int) -> ContainerItem:
        container = ContainerItem(database, colors_for(color_index), color_index, self.display_scale)
        self.addItem(container)
        self.containers[database.name] = container
        return container

    def _add_schema_item(self, container: ContainerItem, database, schema) -> SchemaItem:
        schema_item = SchemaItem(database, schema, container.colors, self.display_scale)
        self.addItem(schema_item)
        self.schema_items[(database.name, schema.name)] = schema_item
        container.schemas.append(schema_item)
        return schema_item

    def _entity_style(self, container: ContainerItem) -> EntityStyle:
        """Entitäten übernehmen den Farbton ihrer Datenbank."""
        colors = container.colors
        return replace(self._base_style, header=colors.qcolor("entity_header"), border=colors.qcolor("border"))

    def _add_entity(self, ref: TableRef, table) -> EntityItem:
        container = self.containers[ref.database]
        item = EntityItem(ref, table, self._entity_style(container), self.display_options)
        item.setScale(self.display_scale)
        self.addItem(item)
        self.entities[ref] = item
        container.members.append(item)
        schema_item = self.schema_items.get((ref.database, ref.schema)) if ref.schema is not None else None
        if schema_item is not None:
            schema_item.members.append(item)
        return item

    def _remove_entity(self, ref: TableRef) -> None:
        item = self.entities.pop(ref)
        for frame in self.frames():
            if item in frame.members:
                frame.members.remove(item)
        self.removeItem(item)

    def _apply_frames(self, layout: Layout) -> None:
        """Von Hand aufgezogene Rahmen aus dem Layout übernehmen."""
        for name, container in self.containers.items():
            frame = layout.frames.get(name)
            container.manual_rect = QRectF(*frame) if frame else None
        for key, schema in self.schema_items.items():
            frame = layout.schema_frames.get(key)
            schema.manual_rect = QRectF(*frame) if frame else None
        self.update_containers()

    def _derived_relationships(self) -> list:
        if self.diagram is None:
            return []
        return apply_overrides(derive_relationships(self.diagram), self.overrides)

    def _build_relations(self) -> None:
        """Linien aus dem Diagramm ableiten (mit Überschreibungen) und anlegen."""
        for item in self.relations:
            self.removeItem(item)
        self.relations = [self._new_relation(rel) for rel in self._derived_relationships()]
        self._sync_dependencies()
        self._apply_entity_corners()

    def _new_relation(self, rel) -> RelationItem:
        item = RelationItem(rel, self.entities[rel.child], self.entities[rel.parent], self.notation)
        item.scale_factor = self.display_scale
        self.addItem(item)
        return item

    # ------------------------------------------------------------------ Gezielt aktualisieren

    def update_diagram(
        self,
        diagram: Diagram,
        layout: Layout | None = None,
        overrides: dict[RelationshipKey, RelationshipOverride] | None = None,
        renamed: dict[TableRef, TableRef] | None = None,
    ) -> bool:
        """Szene an ein geändertes Diagramm anpassen, ohne sie neu aufzubauen.

        Nur gelöschte Tabellen verschwinden, geänderte werden neu berechnet,
        neue kommen hinzu; Linien werden nur dort ersetzt, wo sich die
        Beziehung oder eine ihrer Boxen geändert hat. Auswahl und
        Bildausschnitt bleiben so erhalten.

        `layout` liefert Positionen und Rahmen (wie bei `load`); ohne
        `layout` behalten vorhandene Tabellen ihre Position und neue werden
        unter die vorhandenen gesetzt. `renamed` (alt → neu) lässt
        umbenannte Tabellen ihre Box behalten.

        Ändern sich Datenbanken, Schemas, Farben, Darstellungsgröße,
        -optionen oder Notation, wird komplett neu aufgebaut (`load`).
        Liefert True, wenn gezielt aktualisiert wurde.
        """
        overrides = dict(overrides if overrides is not None else self.overrides)
        if not self._can_update(diagram, layout):
            self.load(diagram, layout, self._base_style, overrides)
            return False
        self.diagram = diagram
        self.overrides = overrides

        # Datenbank- und Schema-Rahmen: nur die Beschreibung tauschen
        for database in diagram.databases:
            container = self.containers[database.name]
            container.database = database
            for schema in database.schemas:
                schema_item = self.schema_items.get((database.name, schema.name))
                if schema_item is not None:
                    schema_item.database, schema_item.schema = database, schema
            for frame in [container, *container.schemas]:
                frame.setToolTip(frame._tooltip())
                frame.update()

        # Tabellen: umbenennen, entfernen, aktualisieren, hinzufügen
        tables = {
            TableRef(db.name, s.name, t.name): t for db in diagram.databases for s in db.schemas for t in s.tables
        }
        for old, new in (renamed or {}).items():
            if old != new and old in self.entities and old not in tables and new in tables and new not in self.entities:
                item = self.entities.pop(old)
                item.set_table(item.table, new)
                self.entities[new] = item
        for ref in [r for r in self.entities if r not in tables]:
            self._remove_entity(ref)
        known = {ref: (item.pos().x(), item.pos().y()) for ref, item in self.entities.items()}
        for ref, table in tables.items():
            item = self.entities.get(ref)
            if item is None:
                self._add_entity(ref, table)
            else:
                item.set_table(table)  # Linien lesen die Spaltenlage beim Führen neu
        # Reihenfolge wie im Diagramm (Auto-Layout, Export)
        self.entities = {ref: self.entities[ref] for ref in tables}

        self._sync_relations()

        if layout is None:  # bekannte Positionen behalten, neue Tabellen platzieren
            layout = replace(self.current_layout(), positions=known)
        self._apply_layout(layout)
        self._apply_frames(layout)
        self.apply_notes(layout.notes)  # Linien zu ersetzten/umbenannten Boxen neu verbinden
        return True

    def _can_update(self, diagram: Diagram, layout: Layout | None) -> bool:
        """Lässt sich gezielt aktualisieren (gleiche Datenbanken, Schemas, Farben, Darstellung)?"""
        old = self.diagram
        if old is None:
            return False
        if [db.name for db in old.databases] != [db.name for db in diagram.databases]:
            return False
        for old_db, new_db in zip(old.databases, diagram.databases):
            if [s.name for s in old_db.schemas] != [s.name for s in new_db.schemas]:
                return False
        if layout is not None:
            colors = self._assign_colors(diagram, layout.database_colors)
            if any(self.containers[name].color_index != index for name, index in colors.items()):
                return False
            if (abs(layout.scale - self.display_scale) > 1e-9 or layout.display != self.display_options
                    or layout.notation != self.notation.key):
                return False
        return True

    def _sync_relations(self) -> None:
        """Linien abgleichen: unveränderte behalten, geänderte ersetzen, fehlende anlegen."""
        reusable: dict[tuple, list[RelationItem]] = defaultdict(list)
        for item in self.relations:
            rel = item.relationship
            alive = (self.entities.get(item.child_item.ref) is item.child_item
                     and self.entities.get(item.parent_item.ref) is item.parent_item)
            if alive:
                reusable[(item.child_item.ref, rel.foreign_key.name)].append(item)
            else:
                self.removeItem(item)
        relations: list[RelationItem] = []
        for rel in self._derived_relationships():
            candidates = reusable.get((rel.child, rel.foreign_key.name), [])
            match = next((c for c in candidates if c.relationship == rel), None)
            if match is not None:
                candidates.remove(match)
                relations.append(match)
            else:
                relations.append(self._new_relation(rel))
        for leftovers in reusable.values():
            for item in leftovers:
                self.removeItem(item)
        self.relations = relations
        self._sync_dependencies()
        self._apply_entity_corners()

    def _sync_dependencies(self) -> None:
        """Abhängigkeitslinien der Views abgleichen (vorhandene Linien bleiben, wo möglich).

        Gezeichnet wird nur, was in der Szene steht – in Teildiagrammen
        entfallen Linien zu nicht gezeigten Objekten.
        """
        wanted: list[tuple[TableRef, TableRef]] = []
        for ref, item in self.entities.items():
            for dep in item.table.view_dependencies if item.table.is_view else ():
                if dep in self.entities and dep != ref and (ref, dep) not in wanted:
                    wanted.append((ref, dep))
        old = {d.key: d for d in self.dependencies}
        result: list[DependencyItem] = []
        for view_ref, target_ref in wanted:
            item = old.pop((view_ref, target_ref), None)
            view, target = self.entities[view_ref], self.entities[target_ref]
            if item is None or item.view_item is not view or item.target_item is not target:
                if item is not None:
                    self.removeItem(item)
                item = DependencyItem(view, target)
                item.scale_factor = self.display_scale
                self.addItem(item)
            result.append(item)
        for item in old.values():
            self.removeItem(item)
        self.dependencies = result

    def _route_dependencies(self) -> None:
        """Abhängigkeitslinien führen: Seitenmitte zu Seitenmitte, anderen Boxen ausweichen."""
        if not self.dependencies:
            return
        f = self.display_scale
        stub = 14.0 * f
        all_rects = {id(item): item.mapRectToScene(item.rect) for item in self.entities.values()}
        for dep in self.dependencies:
            v_rect, t_rect = all_rects[id(dep.view_item)], all_rects[id(dep.target_item)]
            view_side, target_side = choose_sides(v_rect, t_rect, 2 * stub)
            start = QPointF(view_side.x_of(v_rect), v_rect.top() + min(v_rect.height() / 2, 40 * f))
            end = QPointF(target_side.x_of(t_rect), t_rect.top() + min(t_rect.height() / 2, 40 * f))
            skip = {id(dep.view_item), id(dep.target_item)}
            obstacles = [r for key, r in all_rects.items() if key not in skip]
            points = route_avoiding(start, view_side, end, target_side, stub, 0.0, obstacles, 14.0 * f)
            dep.set_route(points, target_side)

    def _apply_entity_corners(self) -> None:
        """IDEF1X: abhängige Entitäten (Kind einer identifizierenden Beziehung) rund, sonst eckig."""
        distinguish = self.notation.distinguishes_dependent_entities
        dependent = {
            r.relationship.child for r in self.relations
            if r.relationship.identifying and r.relationship.child != r.relationship.parent
        }
        for ref, item in self.entities.items():
            item.set_square_corners(distinguish and ref not in dependent)

    def set_notation(self, key: str) -> None:
        """Notation wechseln (Krähenfuß, (min,max), IDEF1X)."""
        if key == self.notation.key:
            return
        self._switch_notation(key)
        self.layout_changed.emit()

    def _switch_notation(self, key: str) -> None:
        self.notation = notation_for(key)
        for item in self.relations:
            item.prepareGeometryChange()
            item.notation = self.notation
        self._apply_entity_corners()
        self.update_relations()

    def set_overrides(self, overrides: dict[RelationshipKey, RelationshipOverride]) -> None:
        """Manuelle Überschreibungen ändern; Linien werden neu aufgebaut."""
        self.overrides = dict(overrides)
        self._build_relations()
        self.update_relations()

    def contextMenuEvent(self, event: QGraphicsSceneContextMenuEvent) -> None:
        for item in self.items(event.scenePos()):  # oberstes Item zuerst
            if isinstance(item, NoteItem):
                self.note_context_requested.emit(item.id, event.screenPos())
            elif isinstance(item, EntityItem):
                self.entity_context_requested.emit(item.ref, event.screenPos())
            elif isinstance(item, RelationItem):
                self.relation_context_requested.emit(item, event.screenPos())
            else:
                continue
            event.accept()
            return
        self.canvas_context_requested.emit(event.scenePos(), event.screenPos())
        event.accept()

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

    def frames(self) -> Iterator[ContainerItem]:
        """Alle Rahmen: erst die Schemas, dann die Datenbanken (Reihenfolge fürs Aktualisieren)."""
        yield from self.schema_items.values()
        yield from self.containers.values()

    @staticmethod
    def _frame_tuple(rect: QRectF | None):
        return None if rect is None else (rect.x(), rect.y(), rect.width(), rect.height())

    def current_layout(self) -> Layout:
        """Aktuelle Positionen, Farben, Rahmen und Darstellungsgröße (zum Speichern)."""
        return Layout(
            positions={ref: (item.pos().x(), item.pos().y()) for ref, item in self.entities.items()},
            database_colors={name: c.color_index for name, c in self.containers.items()},
            frames={n: self._frame_tuple(c.manual_rect) for n, c in self.containers.items() if c.manual_rect is not None},
            schema_frames={
                k: self._frame_tuple(s.manual_rect) for k, s in self.schema_items.items() if s.manual_rect is not None
            },
            scale=self.display_scale,
            display=self.display_options,
            notation=self.notation.key,
            notes=[item.note for item in self.notes.values()],
        )

    # ------------------------------------------------------------------ Anordnen

    def auto_layout(self, notify: bool = True) -> None:
        """Alle Boxen automatisch anordnen: je Datenbank ein Block, darin je Schema."""
        refs_by_db: dict[str, list[TableRef]] = defaultdict(list)
        for ref in self.entities:
            refs_by_db[ref.database].append(ref)
        for frame in self.frames():
            frame.manual_rect = None
        offsets = self._note_offsets()
        self._set_positions(self._layout_databases(refs_by_db, 0.0))
        self._restore_note_offsets(offsets)
        if notify:
            self.layout_changed.emit()

    def _database_gap(self, has_schemas: bool) -> float:
        gap = self._layout_options().group_gap
        return gap + (self.SCHEMA_EXTRA_GAP * self.display_scale if has_schemas else 0.0)

    def _layout_databases(self, refs_by_db: dict[str, list[TableRef]], start_y: float) -> dict[TableRef, Position]:
        """Datenbanken untereinander anordnen, innerhalb je Schema einen Block."""
        options = self._layout_options()
        schema_options = replace(options, group_gap=self.SCHEMA_GAP * self.display_scale)
        edges = [(r.relationship.child, r.relationship.parent) for r in self.relations]
        edges += [d.key for d in self.dependencies]  # Views in die Nähe ihrer Tabellen
        positions: dict[TableRef, Position] = {}
        y = start_y
        for refs in refs_by_db.values():
            has_schemas = any(r.schema is not None for r in refs)
            if y > start_y:
                y += self._database_gap(has_schemas)
            nodes = [LayoutNode(r, self._box(r).width(), self._box(r).height(), r.schema) for r in refs]
            block = layered_layout(nodes, edges, schema_options)
            for ref, (x, by) in block.items():
                positions[ref] = (x, y + by)
            y = max(positions[r][1] + self._box(r).height() for r in refs)
        return positions

    def _set_positions(self, positions: dict[TableRef, Position]) -> None:
        self._routing_suspended = True
        try:
            for ref, (x, y) in positions.items():
                self.entities[ref].setPos(x, y)
        finally:
            self._routing_suspended = False
        self.update_containers()
        self.update_relations()

    def _apply_layout(self, layout: Layout) -> None:
        """Gespeichertes Layout anwenden und neue Tabellen sinnvoll platzieren.

        Neue Tabellen kommen in Zeilen unter die bekannten ihres Schemas; ist
        das Schema neu, unter die bekannten ihrer Datenbank; ist auch die
        Datenbank neu, wird sie automatisch angeordnet und unters Diagramm gesetzt.
        """
        options = self._layout_options()
        positions = {ref: pos for ref, pos in layout.positions.items() if ref in self.entities}
        new_groups: dict[tuple[str, str | None], list[TableRef]] = defaultdict(list)
        for ref in self.entities:
            if ref not in positions:
                new_groups[(ref.database, ref.schema)].append(ref)

        def bottom_of(refs) -> float:
            return max(positions[r][1] + self._box(r).height() for r in refs)

        new_databases: dict[str, list[TableRef]] = defaultdict(list)
        for (db_name, schema), new_refs in new_groups.items():
            same_schema = [r for r in positions if r.database == db_name and r.schema == schema]
            same_db = [r for r in positions if r.database == db_name]
            anchors = same_schema or same_db
            if not anchors:
                new_databases[db_name].extend(new_refs)
                continue
            gap = self.NEW_TABLE_GAP * self.display_scale
            if not same_schema:  # neues Schema: Platz für dessen Rahmen und Kopfzeile
                gap += self.SCHEMA_GAP * self.display_scale
            self._place_rows(new_refs, anchors, positions, bottom_of(anchors) + gap, options)

        if new_databases:
            start = bottom_of(positions) + self._database_gap(True) if positions else 0.0
            positions.update(self._layout_databases(new_databases, start))
        self._set_positions(positions)

    def _place_rows(self, refs, anchors, positions, top: float, options: LayoutOptions) -> None:
        """Boxen zeilenweise unter `anchors` setzen (Breite wie die Anker, mind. 1000)."""
        left = min(positions[r][0] for r in anchors)
        right = max(positions[r][0] + self._box(r).width() for r in anchors)
        max_width = max(right - left, 1000.0)
        x, y, row_h = left, top, 0.0
        for ref in refs:
            rect = self._box(ref)
            if x > left and x + rect.width() > left + max_width:
                x, y, row_h = left, y + row_h + options.row_gap, 0.0
            positions[ref] = (x, y)
            x += rect.width() + options.row_gap * 1.5
            row_h = max(row_h, rect.height())

    def _box(self, ref: TableRef) -> QRectF:
        """Größe einer Box in Szeneneinheiten (inkl. Darstellungsgröße)."""
        rect = self.entities[ref].rect
        return QRectF(0, 0, rect.width() * self.display_scale, rect.height() * self.display_scale)

    def _layout_options(self) -> LayoutOptions:
        """Abstände fürs Auto-Layout bei der aktuellen Darstellungsgröße.

        Die Abstände wachsen nur so weit, wie Linien, Symbole und
        Behälterköpfe es verlangen – größere Darstellung heißt so:
        größere Schrift im Verhältnis zum ganzen Diagramm.
        """
        o, f = LayoutOptions(), self.display_scale
        stub = (self.notation.end_length(ZERO_OR_MANY) + 8) * f
        return replace(
            o,
            column_gap=max(o.column_gap, 2 * stub + 30 * f),  # Platz für zwei Symbole und das Mittelstück
            row_gap=o.row_gap * f**0.5,
            component_gap=o.component_gap * f**0.5,
            group_gap=o.group_gap * f,  # Behälter samt Kopfzeile wachsen voll mit
        )

    # ------------------------------------------------------------------ Darstellungsgröße

    def set_display_scale(self, scale: float) -> None:
        """Alle Boxen, Schriften und Symbole vergrößern/verkleinern.

        Die Anordnung wird um die linke obere Ecke mitskaliert, damit
        nichts überlappt und die Abstände im Verhältnis bleiben.
        """
        scale = round(min(max(scale, MIN_SCALE), MAX_SCALE), 2)
        if abs(scale - self.display_scale) < 1e-9:
            return
        ratio = scale / self.display_scale
        if self.entities:
            ox = min(item.pos().x() for item in self.entities.values())
            oy = min(item.pos().y() for item in self.entities.values())
        else:
            ox = oy = 0.0

        def scaled(x: float, y: float) -> Position:
            return ox + (x - ox) * ratio, oy + (y - oy) * ratio

        self.display_scale = scale
        positions = {ref: scaled(item.pos().x(), item.pos().y()) for ref, item in self.entities.items()}
        for item in self.entities.values():
            item.setScale(scale)
        for rel in [*self.relations, *self.dependencies]:
            rel.scale_factor = scale
        for frame in self.frames():
            frame.set_scale(scale)
            if frame.manual_rect is not None:
                r = frame.manual_rect
                x, y = scaled(r.x(), r.y())
                frame.manual_rect = QRectF(x, y, r.width() * ratio, r.height() * ratio)
        for note in self.notes.values():
            x, y = scaled(note.pos().x(), note.pos().y())
            w, h = note.rect.width() * ratio, note.rect.height() * ratio
            note.set_scale(scale)
            note.set_note(Note(note.id, note.text, (x, y, w, h), note.entity))
        self._set_positions(positions)
        self.layout_changed.emit()

    # ------------------------------------------------------------------ Rückgängig/Wiederholen

    def restore_layout(self, layout: Layout) -> None:
        """Layout-Zustand wiederherstellen, ohne die Szene neu aufzubauen.

        Setzt Darstellungsgröße, Darstellungsoptionen, Positionen und
        Rahmen. Farben bleiben unverändert (sie ändern sich nur mit dem Schema).
        """
        if abs(layout.scale - self.display_scale) > 1e-9:
            self.display_scale = layout.scale
            for item in self.entities.values():
                item.setScale(layout.scale)
            for rel in [*self.relations, *self.dependencies]:
                rel.scale_factor = layout.scale
            for frame in self.frames():
                frame.set_scale(layout.scale)
        if layout.notation != self.notation.key:
            self._switch_notation(layout.notation)
        if layout.display != self.display_options:
            self.display_options = layout.display
            for item in self.entities.values():
                item.set_options(layout.display)
        for name, container in self.containers.items():
            frame = layout.frames.get(name)
            container.manual_rect = QRectF(*frame) if frame else None
        for key, schema in self.schema_items.items():
            frame = layout.schema_frames.get(key)
            schema.manual_rect = QRectF(*frame) if frame else None
        for note in self.notes.values():
            note.set_scale(self.display_scale)
        self._set_positions({ref: pos for ref, pos in layout.positions.items() if ref in self.entities})
        self.apply_notes(layout.notes)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._gesture_before = self.current_layout()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        if event.button() != Qt.MouseButton.LeftButton or self._gesture_before is None:
            return
        before, self._gesture_before = self._gesture_before, None
        after = self.current_layout()
        if after != before:
            self.gesture_finished.emit(before, after, self.describe_change(before, after))

    @staticmethod
    def describe_change(before: Layout, after: Layout) -> str:
        """Kurzer Text für das Menü „Rückgängig: …“."""
        if after.positions != before.positions:
            moved = [ref for ref, pos in after.positions.items() if before.positions.get(ref) != pos]
            return f"„{moved[0].table}“ verschieben" if len(moved) == 1 else f"{len(moved)} Tabellen verschieben"
        if after.frames != before.frames or after.schema_frames != before.schema_frames:
            return "Rahmen aufziehen"
        if after.notes != before.notes:
            old = {n.id: n for n in before.notes}
            changed = [n for n in after.notes if old.get(n.id) != n]
            resized = any(n.id in old and n.frame[2:] != old[n.id].frame[2:] for n in changed)
            return "Notizgröße ändern" if resized else "Notiz verschieben"
        return "Layout ändern"

    # ------------------------------------------------------------------ Darstellungsoptionen

    def set_display_options(self, options: DisplayOptions) -> bool:
        """Spalten, Datentypen, Indexe, Kommentare ein-/ausblenden.

        Positionen bleiben erhalten. Liefert True, wenn sich danach Boxen
        überlappen (dann hilft „Automatisch anordnen“).
        """
        if options == self.display_options:
            return False
        self.display_options = options
        for item in self.entities.values():
            item.set_options(options)
        self.update_containers()
        self.update_relations()
        self.layout_changed.emit()
        return self.has_overlaps()

    def has_overlaps(self) -> bool:
        """Überlappen sich zwei Boxen?"""
        rects = sorted((item.mapRectToScene(item.rect) for item in self.entities.values()), key=lambda r: r.left())
        for i, a in enumerate(rects):
            for b in rects[i + 1 :]:
                if b.left() >= a.right():
                    break  # nach links sortiert: keine weiteren Kandidaten
                if a.intersects(b):
                    return True
        return False

    # ------------------------------------------------------------------ Behälter

    def fit_frames_to_content(self) -> None:
        """Alle von Hand aufgezogenen Rahmen auf ihren Inhalt zurücksetzen."""
        changed = any(f.manual_rect is not None for f in self.frames())
        for frame in self.frames():
            frame.manual_rect = None
        self.update_containers()
        if changed:
            self.layout_changed.emit()

    def container_of(self, item: EntityItem) -> ContainerItem | None:
        return self.containers.get(item.ref.database)

    def schema_of(self, item: EntityItem) -> SchemaItem | None:
        if item.ref.schema is None:
            return None
        return self.schema_items.get((item.ref.database, item.ref.schema))

    def update_containers(self) -> None:
        """Erst Schemas, dann Datenbanken – die umschließen ihre Schemas."""
        for frame in self.frames():
            frame.update_geometry()

    def move_container(self, container: ContainerItem, dx: float, dy: float) -> None:
        """Rahmen samt Entitäten (und inneren Rahmen) verschieben; Linien nur einmal neu führen."""
        for schema in container.schemas:
            if schema.manual_rect is not None:
                schema.manual_rect.translate(dx, dy)
        self._routing_suspended = True
        try:
            for item in container.members:
                item.moveBy(dx, dy)
        finally:
            self._routing_suspended = False
        self.update_containers()
        self.update_relations()
        self.layout_changed.emit()

    # ------------------------------------------------------------------ Notizen

    def apply_notes(self, notes: list[Note]) -> None:
        """Notizen an `notes` angleichen: vorhandene Items behalten, neue anlegen, fehlende entfernen."""
        wanted = {n.id: n for n in notes}
        for note_id in [i for i in self.notes if i not in wanted]:
            self.removeItem(self.notes.pop(note_id))
        result: dict[str, NoteItem] = {}
        for note in notes:
            item = self.notes.get(note.id)
            if item is None:
                item = NoteItem(note, self.display_scale)
                self.addItem(item)
            elif item.note != note:
                item.set_note(note)
            result[note.id] = item
        self.notes = result
        self._update_note_links()

    def note_changed(self, item: NoteItem) -> None:
        """Wird von NoteItem beim Verschieben/Größe ändern aufgerufen."""
        link = self.note_links.get(item.id)
        if link is not None:
            link.update_geometry()
        self.layout_changed.emit()

    def _update_note_links(self) -> None:
        """Linien zu verbundenen Tabellen anlegen, nachführen oder (Tabelle fehlt) entfernen."""
        for note_id, note in self.notes.items():
            entity = self.entities.get(note.entity) if note.entity is not None else None
            link = self.note_links.get(note_id)
            if link is not None and (entity is None or link.entity_item is not entity or link.note_item is not note):
                self.removeItem(self.note_links.pop(note_id))
                link = None
            if entity is not None:
                if link is None:
                    link = NoteLinkItem(note, entity)
                    self.addItem(link)
                    self.note_links[note_id] = link
                link.update_geometry()
        for note_id in [i for i in self.note_links if i not in self.notes]:
            self.removeItem(self.note_links.pop(note_id))

    def _note_offsets(self) -> dict[str, QPointF]:
        """Abstand verbundener Notizen zu ihrer Tabelle (damit sie beim Anordnen mitwandern)."""
        result = {}
        for note in self.notes.values():
            entity = self.entities.get(note.entity) if note.entity is not None else None
            if entity is not None:
                result[note.id] = note.pos() - entity.pos()
        return result

    def _restore_note_offsets(self, offsets: dict[str, QPointF]) -> None:
        for note_id, offset in offsets.items():
            note = self.notes[note_id]
            note.setPos(self.entities[note.entity].pos() + offset)

    def _free_note_spot(self, rect: QRectF, w: float, h: float) -> tuple:
        """Platz für eine Notiz an `rect`: rechts, oben, links, unten – erst nah, dann weiter weg.

        Frei heißt: keine Box und keine andere Notiz im Weg. Findet sich nichts,
        kommt sie rechts neben die Tabelle.
        """
        gap = 40 * self.display_scale
        obstacles = [e.mapRectToScene(e.rect) for e in self.entities.values()]
        obstacles += [n.mapRectToScene(n.rect) for n in self.notes.values()]
        margin = 12 * self.display_scale
        for distance in (gap, gap * 3, gap * 6):
            for x, y in (
                (rect.right() + distance, rect.top()),  # rechts
                (rect.left(), rect.top() - distance - h),  # oben
                (rect.left() - distance - w, rect.top()),  # links
                (rect.left(), rect.bottom() + distance),  # unten
            ):
                spot = QRectF(x, y, w, h).adjusted(-margin, -margin, margin, margin)
                if not any(spot.intersects(o) for o in obstacles):
                    return x, y, w, h
        return rect.right() + gap, rect.top(), w, h

    def new_note_frame(self, entity: TableRef | None = None, at: QPointF | None = None) -> tuple:
        """Rahmen für eine neue Notiz: rechts neben der Tabelle oder an `at` (Szenenkoordinaten)."""
        from erd_editor.diagram.note_item import DEFAULT_SIZE

        w, h = DEFAULT_SIZE[0] * self.display_scale, DEFAULT_SIZE[1] * self.display_scale
        item = self.entities.get(entity) if entity is not None else None
        if item is not None:
            return self._free_note_spot(item.mapRectToScene(item.rect), w, h)
        if at is None:
            bounds = self.itemsBoundingRect()
            at = bounds.center() if not bounds.isNull() else QPointF(0, 0)
        return at.x(), at.y(), w, h

    # ------------------------------------------------------------------ Linienführung

    def entity_moved(self, item: EntityItem) -> None:
        """Wird von EntityItem bei jeder Positionsänderung aufgerufen."""
        if self._routing_suspended:
            return
        schema = self.schema_of(item)
        if schema is not None:
            schema.update_geometry()  # Schema-Rahmen wächst mit …
        container = self.container_of(item)
        if container is not None:
            container.update_geometry()  # … und der Datenbank-Rahmen auch
        if self.relations or self.dependencies:
            self.update_relations()
        self.layout_changed.emit()

    def update_relations(self) -> None:
        """Alle Linien neu führen.

        Schritt 1: Seiten und Kind-Andockpunkte bestimmen.
        Schritt 2: Eltern-Andockpunkte je Box und Seite verteilen.
        Schritt 3: Linien führen.
        """
        self._route_dependencies()
        self._update_note_links()
        if not self.relations:
            return
        f = self.display_scale
        stub = (self.notation.end_length(ZERO_OR_MANY) + 8) * f
        spacing = (2 * self.notation.end_width() + 4) * f

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
            margin = (parent.style.radius + 4) * f
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
                offset = rel.parallel_index * self.PARALLEL_OFFSET * f
            else:
                offset = (rel.parallel_index - (rel.parallel_count - 1) / 2) * self.PARALLEL_OFFSET * f
            skip = {id(rel_item.child_item), id(rel_item.parent_item)}
            obstacles = [r for key, r in all_rects.items() if key not in skip]
            points = route_avoiding(starts[i], child_side, ends[i], parent_side, stub, offset, obstacles, 14.0 * f)
            rel_item.set_route(points, child_side, parent_side)

    def _on_selection_changed(self) -> None:
        for rel_item in [*self.relations, *self.dependencies]:
            rel_item.update()
