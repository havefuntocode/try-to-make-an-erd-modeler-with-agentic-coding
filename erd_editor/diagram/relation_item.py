"""Darstellung einer Beziehung als Linie mit Notationssymbolen an beiden Enden."""

from __future__ import annotations

from html import escape

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPainterPathStroker, QPen
from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem, QWidget

from erd_editor.analysis import Relationship
from erd_editor.diagram.entity_item import EntityItem
from erd_editor.diagram.routing import Side
from erd_editor.notation import Notation

LINE_COLOR = QColor("#4B5563")
HIGHLIGHT_COLOR = QColor("#E07A00")
SYMBOL_FILL = QColor("#FFFFFF")


class RelationItem(QGraphicsItem):
    """Linie zwischen zwei Entitätsboxen.

    Die Geometrie setzt die Szene über `set_route()`, weil die Verteilung
    der Andockpunkte alle Beziehungen einer Box berücksichtigen muss.
    """

    LINE_WIDTH = 1.3
    HIGHLIGHT_WIDTH = 2.2

    def __init__(self, relationship: Relationship, child: EntityItem, parent: EntityItem, notation: Notation) -> None:
        super().__init__()
        self.relationship = relationship
        self.child_item = child
        self.parent_item = parent
        self.notation = notation
        self.scale_factor = 1.0  # Darstellungsgröße, setzt die Szene
        self.points: list[QPointF] = []
        self.child_side = Side.RIGHT
        self.parent_side = Side.LEFT
        self._path = QPainterPath()
        self.setZValue(-1)  # Linien hinter den Boxen
        self.setAcceptHoverEvents(True)
        self.setToolTip(self._tooltip())

    # ------------------------------------------------------------------ Geometrie

    def set_route(self, points: list[QPointF], child_side: Side, parent_side: Side) -> None:
        self.prepareGeometryChange()
        self.points = points
        self.child_side = child_side
        self.parent_side = parent_side
        path = QPainterPath()
        if points:
            path.moveTo(points[0])
            for p in points[1:]:
                path.lineTo(p)
        self._path = path

    def path(self) -> QPainterPath:
        return QPainterPath(self._path)

    def boundingRect(self) -> QRectF:
        m = (self.notation.end_width() + self.HIGHLIGHT_WIDTH + 2) * self.scale_factor
        return self._path.boundingRect().adjusted(-m, -m, m, m)

    def shape(self) -> QPainterPath:
        stroker = QPainterPathStroker()
        stroker.setWidth(8 * self.scale_factor)  # großzügige Trefferfläche für Tooltip
        return stroker.createStroke(self._path)

    # ------------------------------------------------------------------ Zeichnen

    @property
    def highlighted(self) -> bool:
        return self.child_item.isSelected() or self.parent_item.isSelected()

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        if len(self.points) < 2:
            return
        hl = self.highlighted
        color = HIGHLIGHT_COLOR if hl else LINE_COLOR
        width = (self.HIGHLIGHT_WIDTH if hl else self.LINE_WIDTH) * self.scale_factor

        line_pen = QPen(color, width)
        line_pen.setStyle(Qt.PenStyle.SolidLine if self.relationship.identifying else Qt.PenStyle.DashLine)
        line_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(line_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._path)

        rel = self.relationship
        # Am Kind steht, wie viele Kinder ein Elternteil hat – und umgekehrt
        self._draw_end(painter, color, self.points[0], self.child_side, rel.child_end)
        self._draw_end(painter, color, self.points[-1], self.parent_side, rel.parent_end)

    def _draw_end(self, painter: QPainter, color: QColor, tip: QPointF, side: Side, cardinality) -> None:
        """Symbol in Einheitsgröße zeichnen und per Transformation skalieren."""
        painter.save()
        painter.translate(tip)
        painter.scale(self.scale_factor, self.scale_factor)
        painter.setPen(QPen(color, self.HIGHLIGHT_WIDTH if self.highlighted else self.LINE_WIDTH))
        painter.setBrush(SYMBOL_FILL)
        self.notation.draw_end(painter, QPointF(0, 0), side.direction, cardinality)
        painter.restore()

    # ------------------------------------------------------------------ Tooltip

    def _tooltip(self) -> str:
        r = self.relationship
        fk = r.foreign_key
        child = f"{escape(r.child.key)} ({escape(', '.join(fk.columns))})"
        parent = f"{escape(r.parent.key)} ({escape(', '.join(fk.referenced_columns))})"
        kind = "1:1" if r.is_one_to_one else "1:n"
        lines = [
            f"<b>{escape(fk.name)}</b>",
            f"{child} → {parent}",
            f"{kind}, {'identifizierend' if r.identifying else 'nicht identifizierend'}",
            f"Elternteil {r.parent_end}, Kinder {r.child_end}",
        ]
        rules = [f"ON {label} {value}" for label, value in (("UPDATE", fk.on_update), ("DELETE", fk.on_delete)) if value]
        if rules:
            lines.append(escape(", ".join(rules)))
        return "<br>".join(lines)
