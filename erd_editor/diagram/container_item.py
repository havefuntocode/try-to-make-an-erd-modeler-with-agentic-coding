"""Datenbank als Behälter hinter ihren Entitäten.

Größe: mindestens die enthaltenen Entitäten plus Rand – wird eine Entität
an den Rand gezogen, wächst der Behälter mit. Zusätzlich kann der Nutzer
den Rahmen an der linken, rechten und unteren Kante sowie an den unteren
Ecken größer aufziehen (`manual_rect`); kleiner als der Inhalt wird er nie.

Verschieben über die Kopfzeile; dabei wandern alle Entitäten mit.
Klicks auf die freie Fläche gehen an die Ansicht weiter (Auswahlrahmen).
"""

from __future__ import annotations

from enum import Flag, auto
from html import escape
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsObject,
    QGraphicsSceneHoverEvent,
    QGraphicsSceneMouseEvent,
    QStyleOptionGraphicsItem,
    QWidget,
)

from erd_editor.diagram.palette import ContainerColors
from erd_editor.diagram.style import scene_font as _font
from erd_editor.model import Database

if TYPE_CHECKING:
    from erd_editor.diagram.entity_item import EntityItem

DIALECT_NAMES = {"mysql": "MySQL", "postgresql": "PostgreSQL", "mssql": "SQL Server"}


class Edge(Flag):
    """Angefasste Kante(n) beim Aufziehen."""

    NONE = 0
    LEFT = auto()
    RIGHT = auto()
    BOTTOM = auto()


_CURSORS = {
    Edge.LEFT: Qt.CursorShape.SizeHorCursor,
    Edge.RIGHT: Qt.CursorShape.SizeHorCursor,
    Edge.BOTTOM: Qt.CursorShape.SizeVerCursor,
    Edge.BOTTOM | Edge.RIGHT: Qt.CursorShape.SizeFDiagCursor,
    Edge.BOTTOM | Edge.LEFT: Qt.CursorShape.SizeBDiagCursor,
}




class ContainerItem(QGraphicsObject):
    PADDING = 24.0
    RADIUS = 10.0
    BORDER_WIDTH = 1.6
    GRIP = 8.0  # Breite der Anfasszone an den Kanten (Szeneneinheiten bei 100 %)

    def __init__(self, database: Database, colors: ContainerColors, color_index: int = 0, scale: float = 1.0) -> None:
        super().__init__()
        self.database = database
        self.colors = colors
        self.color_index = color_index  # Palettenindex, wird im Layout gespeichert
        self.members: list[EntityItem] = []
        self.manual_rect: QRectF | None = None  # von Hand aufgezogener Rahmen (Szenenkoordinaten)
        self._rect = QRectF()
        self._drag_last: QPointF | None = None
        self._resize_edge = Edge.NONE
        self._resize_start: tuple[QPointF, QRectF] | None = None
        self.set_scale(scale)
        self.setZValue(-2)  # hinter Linien (-1) und Entitäten (0)
        self.setAcceptHoverEvents(True)
        self.setToolTip(self._tooltip())

    # ------------------------------------------------------------------ Darstellungsgröße

    def set_scale(self, scale: float) -> None:
        """Schrift und Abstände an die Darstellungsgröße anpassen."""
        self.scale_factor = scale
        self.title_font = _font(11 * scale, bold=True)
        self.info_font = _font(8.5 * scale)
        fm_title, fm_info = QFontMetricsF(self.title_font), QFontMetricsF(self.info_font)
        self._header_h = fm_title.height() + fm_info.height() + 12 * scale
        self.padding = self.PADDING * scale
        self.radius = self.RADIUS * scale
        self.grip = self.GRIP * scale

    # ------------------------------------------------------------------ Texte

    @property
    def info_text(self) -> str:
        """Zweite Kopfzeile: Dialekt/Version, Zeichensatz/Kollation."""
        db = self.database
        parts = [" ".join(p for p in (DIALECT_NAMES.get(db.dialect.value, db.dialect.value), db.server_version) if p)]
        if db.charset or db.collation:
            parts.append(" / ".join(p for p in (db.charset, db.collation) if p))
        return " · ".join(parts)

    def _tooltip(self) -> str:
        db = self.database
        lines = [f"<b>{escape(db.name)}</b>", escape(self.info_text)]
        if db.connection_profile:
            lines.append(f"Verbindung: {escape(db.connection_profile)}")
        lines.append("Kopfzeile ziehen: verschieben · Rand ziehen: größer aufziehen")
        return "<br>".join(lines)

    # ------------------------------------------------------------------ Geometrie

    @property
    def rect(self) -> QRectF:
        return QRectF(self._rect)

    @property
    def header_rect(self) -> QRectF:
        return QRectF(self._rect.left(), self._rect.top(), self._rect.width(), self._header_h)

    def content_rect(self) -> QRectF:
        """Kleinster Rahmen um die Entitäten (ohne manuelle Vergrößerung)."""
        if not self.members:
            return QRectF(0, 0, 200 * self.scale_factor, self._header_h + 2 * self.padding)
        union = QRectF()
        for item in self.members:
            union = union.united(item.mapRectToScene(item.rect))
        p = self.padding
        rect = union.adjusted(-p, -p - self._header_h, p, p)
        title_w = QFontMetricsF(self.title_font).horizontalAdvance(self.database.name)
        info_w = QFontMetricsF(self.info_font).horizontalAdvance(self.info_text)
        min_w = max(title_w, info_w) + 2 * p
        if rect.width() < min_w:
            rect.setWidth(min_w)
        return rect

    def update_geometry(self) -> None:
        """Rahmen = Inhalt, vereinigt mit dem von Hand aufgezogenen Rahmen."""
        self.prepareGeometryChange()
        rect = self.content_rect()
        if self.manual_rect is not None:
            rect = rect.united(self.manual_rect)
        self._rect = rect

    def fit_to_content(self) -> None:
        """Von Hand aufgezogene Vergrößerung verwerfen."""
        self.manual_rect = None
        self.update_geometry()

    def boundingRect(self) -> QRectF:
        m = self.BORDER_WIDTH * self.scale_factor
        return self._rect.adjusted(-m, -m, m, m)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(self._rect, self.radius, self.radius)
        return path

    def edge_at(self, pos: QPointF) -> Edge:
        """Welche Kante(n) liegen unter dem Punkt? (oben ist die Kopfzeile)"""
        r, g = self._rect, self.grip
        if not r.adjusted(-g, -g, g, g).contains(pos) or pos.y() < r.top() + self._header_h:
            return Edge.NONE
        edge = Edge.NONE
        if abs(pos.x() - r.left()) <= g:
            edge |= Edge.LEFT
        elif abs(pos.x() - r.right()) <= g:
            edge |= Edge.RIGHT
        if abs(pos.y() - r.bottom()) <= g:
            edge |= Edge.BOTTOM
        return edge

    # ------------------------------------------------------------------ Zeichnen

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        if self._rect.isNull():
            return
        c = self.colors
        s = self.scale_factor
        outline = self.shape()

        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(c.qcolor("fill"))
        painter.drawPath(outline)
        painter.setClipPath(outline)
        painter.fillRect(self.header_rect, c.qcolor("band"))
        painter.restore()

        painter.setPen(QPen(c.qcolor("border"), 1 * s))
        hr = self.header_rect
        painter.drawLine(hr.bottomLeft(), hr.bottomRight())

        p = self.padding / 2
        fm_title = QFontMetricsF(self.title_font)
        painter.setPen(c.qcolor("border").darker(150))
        painter.setFont(self.title_font)
        painter.drawText(
            QRectF(hr.left() + p, hr.top() + 4 * s, hr.width() - 2 * p, fm_title.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.database.name,
        )
        painter.setFont(self.info_font)
        painter.setPen(c.qcolor("border").darker(120))
        painter.drawText(
            QRectF(hr.left() + p, hr.top() + 4 * s + fm_title.height(), hr.width() - 2 * p,
                   hr.height() - fm_title.height() - 6 * s),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.info_text,
        )

        painter.setPen(QPen(c.qcolor("border"), self.BORDER_WIDTH * s))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(outline)

        # Griff unten rechts als Hinweis aufs Aufziehen
        grip_pen = QPen(c.qcolor("border"), 1.2 * s)
        painter.setPen(grip_pen)
        br = self._rect.bottomRight() - QPointF(5 * s, 5 * s)
        for d in (4, 8, 12):
            painter.drawLine(br + QPointF(-d * s, 0), br + QPointF(0, -d * s))

    # ------------------------------------------------------------------ Maus

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        edge = self.edge_at(event.pos())
        if edge != Edge.NONE:
            self._resize_edge = edge
            self._resize_start = (event.scenePos(), QRectF(self._rect))
            event.accept()
        elif self.header_rect.contains(event.pos()):
            self._drag_last = event.scenePos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        else:
            event.ignore()  # freie Fläche: Auswahlrahmen der Ansicht

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._resize_start is not None:
            start, rect = self._resize_start
            delta = event.scenePos() - start
            self.resize_to(self._resized(rect, self._resize_edge, delta.x(), delta.y()))
            event.accept()
        elif self._drag_last is not None:
            delta = event.scenePos() - self._drag_last
            self._drag_last = event.scenePos()
            self.move_by(delta.x(), delta.y())
            event.accept()
        else:
            event.ignore()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._resize_start is not None or self._drag_last is not None:
            self._resize_start = None
            self._resize_edge = Edge.NONE
            self._drag_last = None
            self.unsetCursor()
            event.accept()
        else:
            event.ignore()

    def hoverMoveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        edge = self.edge_at(event.pos())
        if edge in _CURSORS:
            self.setCursor(_CURSORS[edge])
        elif self.header_rect.contains(event.pos()):
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.unsetCursor()
        super().hoverMoveEvent(event)

    @staticmethod
    def _resized(rect: QRectF, edge: Edge, dx: float, dy: float) -> QRectF:
        r = QRectF(rect)
        if Edge.LEFT in edge:
            r.setLeft(r.left() + dx)
        if Edge.RIGHT in edge:
            r.setRight(r.right() + dx)
        if Edge.BOTTOM in edge:
            r.setBottom(r.bottom() + dy)
        return r.normalized()

    # ------------------------------------------------------------------ Aktionen

    def resize_to(self, rect: QRectF) -> None:
        """Rahmen aufziehen; kleiner als der Inhalt wird er nicht."""
        self.manual_rect = QRectF(rect)
        self.update_geometry()
        scene = self.scene()
        if scene is not None and hasattr(scene, "layout_changed"):
            scene.layout_changed.emit()

    def move_by(self, dx: float, dy: float) -> None:
        """Behälter samt Entitäten (und aufgezogenem Rahmen) verschieben."""
        if self.manual_rect is not None:
            self.manual_rect.translate(dx, dy)
        scene = self.scene()
        if scene is not None and hasattr(scene, "move_container"):
            scene.move_container(self, dx, dy)
        else:
            for item in self.members:
                item.moveBy(dx, dy)
            self.update_geometry()
