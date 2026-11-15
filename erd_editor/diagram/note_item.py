"""Notizen (Infoboxen): gelber Zettel mit umgeknickter Ecke, optional mit einer Tabelle verbunden.

Die Notiz liegt in Szenenkoordinaten (ohne `setScale`); die Szene skaliert
Rahmen und Schrift mit der Darstellungsgröße. Größe ändern über den
Anfasser rechts unten; Doppelklick meldet `note_activated` (Bearbeiten).
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsSceneHoverEvent,
    QGraphicsSceneMouseEvent,
    QStyleOptionGraphicsItem,
    QWidget,
)

from erd_editor.model import Note, TableRef

FILL = QColor("#FFF6C4")
FOLD = QColor("#EADB8C")
BORDER = QColor("#C9A93A")
SELECTED = QColor("#E07A00")
TEXT = QColor("#3D3A2A")
PLACEHOLDER = QColor("#9A9070")
LINK = QColor("#B39A3E")

DEFAULT_SIZE = (200.0, 90.0)  # bei 100 %
MIN_SIZE = (80.0, 40.0)
FONT_PT = 9.0
PADDING = 8.0
FOLD_SIZE = 12.0
GRIP = 12.0


class NoteItem(QGraphicsObject):
    """Ein Notizzettel. `note` liefert den aktuellen Stand als `Note` (für das Layout)."""

    Z = 0.5  # über Linien und Rahmen, unter einer ausgewählten Box

    def __init__(self, note: Note, scale: float = 1.0) -> None:
        super().__init__()
        self.id = note.id
        self.text = note.text
        self.entity: TableRef | None = note.entity
        self.scale_factor = scale
        x, y, w, h = note.frame
        self._size = (w, h)
        self.setPos(x, y)
        self._resize_start: tuple[QPointF, tuple[float, float]] | None = None
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setZValue(self.Z)

    # ------------------------------------------------------------------ Zustand

    @property
    def note(self) -> Note:
        w, h = self._size
        return Note(self.id, self.text, (self.pos().x(), self.pos().y(), w, h), self.entity)

    def set_note(self, note: Note) -> None:
        """Stand aus dem Layout übernehmen (Rückgängig, Laden)."""
        x, y, w, h = note.frame
        self.prepareGeometryChange()
        self.text, self.entity, self._size = note.text, note.entity, (w, h)
        self.setPos(x, y)
        self.update()

    def set_size(self, width: float, height: float) -> None:
        f = self.scale_factor
        width, height = max(width, MIN_SIZE[0] * f), max(height, MIN_SIZE[1] * f)
        if (width, height) != self._size:
            self.prepareGeometryChange()
            self._size = (width, height)
            self._notify()

    def set_scale(self, scale: float) -> None:
        self.scale_factor = scale
        self.update()

    @property
    def rect(self) -> QRectF:
        return QRectF(0, 0, *self._size)

    def boundingRect(self) -> QRectF:
        return self.rect.adjusted(-2, -2, 2, 2)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(self.rect)
        return path

    @property
    def font(self) -> QFont:
        font = QFont("Segoe UI")
        font.setPointSizeF(FONT_PT * self.scale_factor)
        return font

    # ------------------------------------------------------------------ Zeichnen

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        f = self.scale_factor
        w, h = self._size
        fold = FOLD_SIZE * f
        outline = QPolygonF([QPointF(0, 0), QPointF(w - fold, 0), QPointF(w, fold), QPointF(w, h), QPointF(0, h)])
        painter.setPen(QPen(SELECTED if self.isSelected() else BORDER, (2.0 if self.isSelected() else 1.0) * f))
        painter.setBrush(FILL)
        painter.drawPolygon(outline)
        # umgeknickte Ecke
        painter.setPen(QPen(BORDER, 1.0 * f))
        painter.setBrush(FOLD)
        painter.drawPolygon(QPolygonF([QPointF(w - fold, 0), QPointF(w - fold, fold), QPointF(w, fold)]))
        # Text
        p = PADDING * f
        painter.setFont(self.font)
        painter.setPen(TEXT if self.text else PLACEHOLDER)
        text = self.text or "Doppelklick zum Bearbeiten"
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap
        painter.save()
        painter.setClipRect(QRectF(p, p, w - 2 * p - fold / 2, h - 2 * p))
        painter.drawText(QRectF(p, p, w - 2 * p - fold / 2, h - 2 * p), int(flags), text)
        painter.restore()
        # Anfasser für die Größe (nur wenn ausgewählt)
        if self.isSelected():
            g = GRIP * f
            painter.setPen(QPen(BORDER, 1.0 * f))
            for i in (0.3, 0.6, 0.9):
                painter.drawLine(QPointF(w - g * i, h - 2 * f), QPointF(w - 2 * f, h - g * i))

    # ------------------------------------------------------------------ Interaktion

    def _in_grip(self, pos: QPointF) -> bool:
        g = GRIP * self.scale_factor
        w, h = self._size
        return pos.x() >= w - g and pos.y() >= h - g

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._in_grip(event.pos()):
            self._resize_start = (event.scenePos(), self._size)
            self.setSelected(True)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._resize_start is not None:
            start, (w, h) = self._resize_start
            delta = event.scenePos() - start
            self.set_size(w + delta.x(), h + delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self._resize_start = None
        super().mouseReleaseEvent(event)

    def hoverMoveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        self.setCursor(Qt.CursorShape.SizeFDiagCursor if self._in_grip(event.pos()) else Qt.CursorShape.ArrowCursor)
        tip = "Doppelklick: bearbeiten · Rechtsklick: Menü · Ecke rechts unten: Größe"
        if self.entity is not None:
            tip = f"Notiz zu <b>{escape(self.entity.key)}</b><br>{tip}"
        self.setToolTip(tip)
        super().hoverMoveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        scene = self.scene()
        if scene is not None and hasattr(scene, "note_activated"):
            # wie bei Tabellen: Dialog erst nach dem Mausereignis öffnen
            event.accept()
            QTimer.singleShot(0, lambda s=scene, i=self.id: _activate(s, i))
            return
        super().mouseDoubleClickEvent(event)

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self._notify()
        return super().itemChange(change, value)

    def _notify(self) -> None:
        scene = self.scene()
        if scene is not None and hasattr(scene, "note_changed"):
            scene.note_changed(self)


def _activate(scene, note_id: str) -> None:
    try:
        grabber = scene.mouseGrabberItem()
    except RuntimeError:  # Szene inzwischen gelöscht
        return
    if grabber is not None:
        grabber.ungrabMouse()
    scene.note_activated.emit(note_id)


# ---------------------------------------------------------------------- Verbindungslinie


def clip_to_rect(rect: QRectF, inside: QPointF, outside: QPointF) -> QPointF:
    """Schnittpunkt der Strecke `inside`→`outside` mit dem Rand von `rect`."""
    line = QLineF(inside, outside)
    edges = [
        QLineF(rect.topLeft(), rect.topRight()),
        QLineF(rect.topRight(), rect.bottomRight()),
        QLineF(rect.bottomRight(), rect.bottomLeft()),
        QLineF(rect.bottomLeft(), rect.topLeft()),
    ]
    for edge in edges:
        kind, point = line.intersects(edge)
        if kind == QLineF.IntersectionType.BoundedIntersection:
            return point
    return inside


class NoteLinkItem(QGraphicsItem):
    """Dünne gestrichelte Linie zwischen Notiz und Tabelle (Rand zu Rand)."""

    def __init__(self, note: NoteItem, entity) -> None:
        super().__init__()
        self.note_item = note
        self.entity_item = entity
        self.line = QLineF()
        self.setZValue(-0.5)  # hinter Notizen und Boxen

    def update_geometry(self) -> None:
        a = self.note_item.mapRectToScene(self.note_item.rect)
        b = self.entity_item.mapRectToScene(self.entity_item.rect)
        if a.intersects(b):
            line = QLineF()
        else:
            line = QLineF(clip_to_rect(a, a.center(), b.center()), clip_to_rect(b, b.center(), a.center()))
        if line != self.line:
            self.prepareGeometryChange()
            self.line = line

    def boundingRect(self) -> QRectF:
        return QRectF(self.line.p1(), self.line.p2()).normalized().adjusted(-4, -4, 4, 4)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        if self.line.isNull():
            return
        f = self.note_item.scale_factor
        pen = QPen(LINK, 1.2 * f)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(self.line)
        painter.setBrush(LINK)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(self.line.p2(), 2.5 * f, 2.5 * f)  # Punkt an der Tabelle
