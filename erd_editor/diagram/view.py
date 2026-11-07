"""Ansicht mit Zoom (Mausrad) und Verschieben (mittlere Maustaste)."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QMouseEvent, QPainter, QWheelEvent
from PySide6.QtWidgets import QGraphicsView


class DiagramView(QGraphicsView):
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

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_start = event.position().toPoint()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
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
        if event.button() == Qt.MouseButton.MiddleButton and self._pan_start is not None:
            self._pan_start = None
            self.viewport().unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)
