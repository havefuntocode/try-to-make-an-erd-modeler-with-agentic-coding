"""Ansicht mit Zoom (Mausrad), Verschieben (mittlere Maustaste) und Beziehungs-Werkzeug."""

from __future__ import annotations

from PySide6.QtCore import QLineF, QPoint, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import QGraphicsLineItem, QGraphicsView


class DiagramView(QGraphicsView):
    """Zeichenfläche.

    Im Beziehungsmodus (`set_relation_mode(True)`) zieht man von der
    Kindtabelle zur Elterntabelle; beim Loslassen kommt `relation_drawn`
    (Kind, Elternteil). Esc oder ein Klick ins Leere bricht ab.
    """

    relation_drawn = Signal(object, object)  # TableRef Kind, TableRef Elternteil
    relation_mode_changed = Signal(bool)

    MIN_ZOOM = 0.1
    MAX_ZOOM = 5.0
    ZOOM_STEP = 1.15

    def __init__(self, scene=None, parent=None) -> None:
        super().__init__(scene, parent)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self._pan_start: QPoint | None = None
        self.relation_mode = False
        self._relation_start = None  # EntityItem, an dem die Linie beginnt
        self._rubber_line: QGraphicsLineItem | None = None

    # ------------------------------------------------------------------ Zoom

    @property
    def zoom(self) -> float:
        return self.transform().m11()

    def set_zoom(self, factor: float) -> None:
        factor = max(self.MIN_ZOOM, min(self.MAX_ZOOM, factor))
        self.scale(factor / self.zoom, factor / self.zoom)

    def zoom_in(self) -> None:
        self.set_zoom(self.zoom * self.ZOOM_STEP)

    def zoom_out(self) -> None:
        self.set_zoom(self.zoom / self.ZOOM_STEP)

    def zoom_to_fit(self) -> None:
        if self.scene() is None or not self.scene().items():
            return
        rect = self.scene().itemsBoundingRect().adjusted(-40, -40, 40, 40)
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        if self.zoom > 1.0:  # kleine Diagramme nicht über 100 % aufblasen
            self.set_zoom(1.0)

    def wheelEvent(self, event: QWheelEvent) -> None:
        steps = event.angleDelta().y() / 120
        if steps:
            self.set_zoom(self.zoom * self.ZOOM_STEP**steps)
        event.accept()

    # ------------------------------------------------------------------ Verschieben

    # ------------------------------------------------------------------ Beziehungs-Werkzeug

    def set_relation_mode(self, on: bool) -> None:
        if on == self.relation_mode:
            return
        self.relation_mode = on
        self._cancel_rubber_line()
        if on:
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
        else:
            self.viewport().unsetCursor()
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.relation_mode_changed.emit(on)

    def entity_at(self, view_pos: QPoint):
        """Oberste Entitätsbox unter einem Punkt (oder None)."""
        for item in self.items(view_pos):
            if hasattr(item, "ref") and hasattr(item, "column_anchor_y"):
                return item
        return None

    def _cancel_rubber_line(self) -> None:
        if self._rubber_line is not None and self._rubber_line.scene() is not None:
            self.scene().removeItem(self._rubber_line)
        self._rubber_line = None
        self._relation_start = None

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and self.relation_mode:
            self.set_relation_mode(False)
            event.accept()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------ Maus

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.relation_mode and event.button() == Qt.MouseButton.LeftButton:
            start = self.entity_at(event.position().toPoint())
            if start is None:
                self.set_relation_mode(False)  # Klick ins Leere: abbrechen
            else:
                self._relation_start = start
                p = self.mapToScene(event.position().toPoint())
                self._rubber_line = QGraphicsLineItem(QLineF(p, p))
                pen = QPen(QColor("#E07A00"), 2, Qt.PenStyle.DashLine)
                pen.setCosmetic(True)  # gleich dick bei jedem Zoom
                self._rubber_line.setPen(pen)
                self._rubber_line.setZValue(10)
                self.scene().addItem(self._rubber_line)
            event.accept()
            return
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_start = event.position().toPoint()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.relation_mode:
            if self._rubber_line is not None:
                line = self._rubber_line.line()
                line.setP2(self.mapToScene(event.position().toPoint()))
                self._rubber_line.setLine(line)
            event.accept()
            return
        if self._pan_start is not None:
            pos = event.position().toPoint()
            delta = pos - self._pan_start
            self._pan_start = pos
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.relation_mode and event.button() == Qt.MouseButton.LeftButton:
            start = self._relation_start
            end = self.entity_at(event.position().toPoint())
            self.set_relation_mode(False)
            if start is not None and end is not None:
                self.relation_drawn.emit(start.ref, end.ref)
            event.accept()
            return
        if event.button() == Qt.MouseButton.MiddleButton and self._pan_start is not None:
            self._pan_start = None
            self.viewport().unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)
