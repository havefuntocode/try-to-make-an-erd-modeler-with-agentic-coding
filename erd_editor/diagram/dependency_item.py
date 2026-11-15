"""Abhängigkeit einer View: gepunktete Linie von der View zu einem benutzten Objekt.

Die Linie endet mit einer offenen Pfeilspitze an der Tabelle (oder View),
die die View benutzt. Die Geometrie setzt die Szene über `set_route()`.
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPainterPathStroker, QPen
from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem, QWidget

from erd_editor.diagram.entity_item import EntityItem
from erd_editor.diagram.routing import Side

LINE_COLOR = QColor("#8A94A6")
HIGHLIGHT_COLOR = QColor("#E07A00")


class DependencyItem(QGraphicsItem):
    """„View benutzt Objekt“ – gepunktet, ohne Notationssymbole."""

    LINE_WIDTH = 1.2
    HIGHLIGHT_WIDTH = 2.0
    ARROW_LENGTH = 9.0
    ARROW_WIDTH = 4.5

    def __init__(self, view: EntityItem, target: EntityItem) -> None:
        super().__init__()
        self.view_item = view
        self.target_item = target
        self.scale_factor = 1.0
        self.points: list[QPointF] = []
        self.end_side = Side.LEFT
        self._path = QPainterPath()
        self.setZValue(-1)  # wie Beziehungen hinter den Boxen
        self.setAcceptHoverEvents(True)
        self.setToolTip(self._tooltip())

    @property
    def key(self):
        return self.view_item.ref, self.target_item.ref

    # ------------------------------------------------------------------ Geometrie

    def set_route(self, points: list[QPointF], end_side: Side) -> None:
        self.prepareGeometryChange()
        self.points = points
        self.end_side = end_side
        path = QPainterPath()
        if points:
            path.moveTo(points[0])
            for p in points[1:]:
                path.lineTo(p)
        self._path = path
        self.setToolTip(self._tooltip())

    def boundingRect(self) -> QRectF:
        m = (self.ARROW_LENGTH + self.HIGHLIGHT_WIDTH + 2) * self.scale_factor
        return self._path.boundingRect().adjusted(-m, -m, m, m)

    def shape(self) -> QPainterPath:
        stroker = QPainterPathStroker()
        stroker.setWidth(8 * self.scale_factor)
        return stroker.createStroke(self._path)

    # ------------------------------------------------------------------ Zeichnen

    @property
    def highlighted(self) -> bool:
        return self.view_item.isSelected() or self.target_item.isSelected()

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        if len(self.points) < 2:
            return
        hl = self.highlighted
        color = HIGHLIGHT_COLOR if hl else LINE_COLOR
        pen = QPen(color, (self.HIGHLIGHT_WIDTH if hl else self.LINE_WIDTH) * self.scale_factor)
        pen.setStyle(Qt.PenStyle.DotLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._path)

        # offene Pfeilspitze am benutzten Objekt (zeigt in die Box hinein)
        tip = self.points[-1]
        out = self.end_side.direction  # von der Box weg
        length, width = self.ARROW_LENGTH * self.scale_factor, self.ARROW_WIDTH * self.scale_factor
        base = QPointF(tip.x() + out.x() * length, tip.y())
        solid = QPen(pen)
        solid.setStyle(Qt.PenStyle.SolidLine)
        painter.setPen(solid)
        painter.drawPolyline([QPointF(base.x(), base.y() - width), tip, QPointF(base.x(), base.y() + width)])

    def _tooltip(self) -> str:
        return (f"<b>{escape(self.view_item.ref.key)}</b> benutzt<br>{escape(self.target_item.ref.key)}"
                "<br><span style='color:#6B7280'>View-Abhängigkeit</span>")
