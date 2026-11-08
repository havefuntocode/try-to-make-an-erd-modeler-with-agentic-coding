"""IDEF1X-Notation (FIPS 184).

- Kindseite: gefüllter Punkt, dazu ein Kennbuchstabe für die Kardinalität:
  kein Zeichen = null, eins oder mehr; **P** = eins oder mehr;
  **Z** = null oder eins; **1** = genau eins.
- Elternseite: offene Raute, wenn die Beziehung optional ist (NULL-fähiger,
  nicht identifizierender FK); sonst kein Symbol.
- Identifizierend durchgezogen, nicht identifizierend gestrichelt (wie bisher).
- Abhängige Entitäten (Kind einer identifizierenden Beziehung) haben runde,
  unabhängige eckige Ecken – das setzt die Szene um.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QFont, QFontMetricsF, QPainter, QPolygonF

from erd_editor.analysis import Cardinality
from erd_editor.notation.base import Notation


def child_marker(cardinality: Cardinality) -> str:
    """Kennbuchstabe an der Kindseite."""
    if cardinality.is_many:
        return "P" if cardinality.minimum >= 1 else ""
    return "1" if cardinality.minimum >= 1 else "Z"


class Idef1xNotation(Notation):
    key = "idef1x"
    name = "IDEF1X"
    distinguishes_dependent_entities = True

    DOT_RADIUS = 3.5
    DOT_DISTANCE = 4.0  # Mittelpunkt des Punkts vom Rand
    DIAMOND = 5.0  # halbe Diagonale der Raute
    DIAMOND_DISTANCE = 8.0

    def __init__(self) -> None:
        self.font = QFont("Segoe UI")
        self.font.setPixelSize(11)
        self.font.setBold(True)
        self._fm = QFontMetricsF(self.font)

    def end_length(self, cardinality: Cardinality) -> float:
        return 24.0

    def end_width(self) -> float:
        return max(self.DIAMOND, self._fm.height() / 2) + 3

    def draw_end(
        self,
        painter: QPainter,
        tip: QPointF,
        direction: QPointF,
        cardinality: Cardinality,
        opposite: Cardinality | None = None,
        at_child: bool = True,
    ) -> None:
        if at_child:
            self._draw_child_end(painter, tip, direction, cardinality)
        else:
            self._draw_parent_end(painter, tip, direction, cardinality)

    def _draw_child_end(self, painter: QPainter, tip: QPointF, direction: QPointF, cardinality: Cardinality) -> None:
        center = tip + direction * self.DOT_DISTANCE
        painter.save()
        painter.setBrush(QBrush(painter.pen().color()))
        painter.drawEllipse(center, self.DOT_RADIUS, self.DOT_RADIUS)
        marker = child_marker(cardinality)
        if marker:
            painter.setFont(self.font)
            w, h = self._fm.horizontalAdvance(marker), self._fm.height()
            x = tip.x() + 8 if direction.x() >= 0 else tip.x() - 8 - w
            painter.drawText(QRectF(x, tip.y() - 2 - h, w, h), Qt.AlignmentFlag.AlignCenter, marker)
        painter.restore()

    def _draw_parent_end(self, painter: QPainter, tip: QPointF, direction: QPointF, cardinality: Cardinality) -> None:
        if not cardinality.is_optional:
            return  # verpflichtend: kein Symbol
        normal = QPointF(-direction.y(), direction.x())
        c = tip + direction * self.DIAMOND_DISTANCE
        d = self.DIAMOND
        diamond = QPolygonF([c - direction * d, c + normal * d, c + direction * d, c - normal * d])
        painter.drawPolygon(diamond)  # Pinsel ist weiß gesetzt → offene Raute
