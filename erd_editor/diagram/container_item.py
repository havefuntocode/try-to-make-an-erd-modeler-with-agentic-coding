"""Datenbank als Behälter hinter ihren Entitäten.

Die Größe ergibt sich immer aus den enthaltenen Entitäten plus Rand:
Wird eine Entität an den Rand gezogen, wächst der Behälter mit.
Verschoben wird der Behälter über seine Kopfzeile; dabei wandern alle
Entitäten mit. Klicks auf die freie Fläche gehen an die Ansicht weiter
(Auswahlrahmen).
"""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsObject,
    QGraphicsSceneHoverEvent,
    QGraphicsSceneMouseEvent,
    QStyleOptionGraphicsItem,
    QWidget,
)

from erd_editor.diagram.palette import ContainerColors
from erd_editor.model import Database

if TYPE_CHECKING:
    from erd_editor.diagram.entity_item import EntityItem

DIALECT_NAMES = {"mysql": "MySQL", "postgresql": "PostgreSQL", "mssql": "SQL Server"}


def _font(size: float, bold: bool = False) -> QFont:
    f = QFont("Segoe UI")
    f.setPointSizeF(size)
    f.setBold(bold)
    return f


class ContainerItem(QGraphicsObject):
    PADDING = 24.0
    RADIUS = 10.0
    BORDER_WIDTH = 1.6

    def __init__(self, database: Database, colors: ContainerColors) -> None:
        super().__init__()
        self.database = database
        self.colors = colors
        self.members: list[EntityItem] = []
        self.title_font = _font(11, bold=True)
        self.info_font = _font(8.5)
        fm_title, fm_info = QFontMetricsF(self.title_font), QFontMetricsF(self.info_font)
        self._header_h = fm_title.height() + fm_info.height() + 12
        self._rect = QRectF()
        self._drag_last: QPointF | None = None
        self.setZValue(-2)  # hinter Linien (-1) und Entitäten (0)
        self.setAcceptHoverEvents(True)
        self.setToolTip(self._tooltip())

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
        lines.append("Kopfzeile ziehen, um die Datenbank zu verschieben")
        return "<br>".join(lines)

    # ------------------------------------------------------------------ Geometrie

    @property
    def rect(self) -> QRectF:
        return QRectF(self._rect)

    @property
    def header_rect(self) -> QRectF:
        return QRectF(self._rect.left(), self._rect.top(), self._rect.width(), self._header_h)

    def update_geometry(self) -> None:
        """Größe an die enthaltenen Entitäten anpassen (Szenenkoordinaten, pos = 0/0)."""
        self.prepareGeometryChange()
        if not self.members:
            self._rect = QRectF(0, 0, 200, self._header_h + 2 * self.PADDING)
            return
        union = QRectF()
        for item in self.members:
            union = union.united(item.mapRectToScene(item.rect))
        p = self.PADDING
        self._rect = union.adjusted(-p, -p - self._header_h, p, p)
        # Mindestbreite für die Kopfzeile
        title_w = QFontMetricsF(self.title_font).horizontalAdvance(self.database.name)
        info_w = QFontMetricsF(self.info_font).horizontalAdvance(self.info_text)
        min_w = max(title_w, info_w) + 2 * p
        if self._rect.width() < min_w:
            self._rect.setWidth(min_w)

    def boundingRect(self) -> QRectF:
        m = self.BORDER_WIDTH
        return self._rect.adjusted(-m, -m, m, m)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(self._rect, self.RADIUS, self.RADIUS)
        return path

    # ------------------------------------------------------------------ Zeichnen

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        if self._rect.isNull():
            return
        c = self.colors
        outline = self.shape()

        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(c.qcolor("fill"))
        painter.drawPath(outline)
        painter.setClipPath(outline)
        painter.fillRect(self.header_rect, c.qcolor("band"))
        painter.restore()

        painter.setPen(QPen(c.qcolor("border"), 1))
        hr = self.header_rect
        painter.drawLine(hr.bottomLeft(), hr.bottomRight())

        p = self.PADDING / 2
        fm_title = QFontMetricsF(self.title_font)
        painter.setPen(c.qcolor("border").darker(150))
        painter.setFont(self.title_font)
        painter.drawText(QRectF(hr.left() + p, hr.top() + 4, hr.width() - 2 * p, fm_title.height()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.database.name)
        painter.setFont(self.info_font)
        painter.setPen(c.qcolor("border").darker(120))
        painter.drawText(QRectF(hr.left() + p, hr.top() + 4 + fm_title.height(), hr.width() - 2 * p, hr.height() - fm_title.height() - 6),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.info_text)

        painter.setPen(QPen(c.qcolor("border"), self.BORDER_WIDTH))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(outline)

    # ------------------------------------------------------------------ Verschieben über die Kopfzeile

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.header_rect.contains(event.pos()):
            self._drag_last = event.scenePos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        else:
            event.ignore()  # freie Fläche: Auswahlrahmen der Ansicht

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._drag_last is None:
            event.ignore()
            return
        delta = event.scenePos() - self._drag_last
        self._drag_last = event.scenePos()
        self.move_by(delta.x(), delta.y())
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._drag_last is not None:
            self._drag_last = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
        else:
            event.ignore()

    def hoverMoveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        if self.header_rect.contains(event.pos()):
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.unsetCursor()
        super().hoverMoveEvent(event)

    def move_by(self, dx: float, dy: float) -> None:
        """Behälter samt Entitäten verschieben."""
        scene = self.scene()
        if scene is not None and hasattr(scene, "move_container"):
            scene.move_container(self, dx, dy)
        else:
            for item in self.members:
                item.moveBy(dx, dy)
            self.update_geometry()
