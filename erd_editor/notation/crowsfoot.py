"""Krähenfuß-Notation (Information Engineering).

Am Entitätsrand steht die Maximalkardinalität (Strich = eins, Krähenfuß = viele),
weiter außen die Minimalkardinalität (Strich = eins, Kreis = null).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainter

from erd_editor.analysis import Cardinality
from erd_editor.notation.base import Notation


class CrowsFootNotation(Notation):
    name = "Krähenfuß"

    FOOT_LENGTH = 12.0  # Länge des Krähenfußes entlang der Linie
    HALF_WIDTH = 6.0  # halbe Breite von Fuß und Strichen
    MAX_BAR = 7.0  # Abstand des Max-Strichs vom Rand
    MIN_BAR = 16.0  # Abstand des Min-Strichs vom Rand
    CIRCLE_CENTER = 19.5
    CIRCLE_RADIUS = 3.5

    def end_length(self, cardinality: Cardinality) -> float:
        return self.CIRCLE_CENTER + self.CIRCLE_RADIUS + 3

    def end_width(self) -> float:
        return self.HALF_WIDTH

    def draw_end(self, painter: QPainter, tip: QPointF, direction: QPointF, cardinality: Cardinality) -> None:
        normal = QPointF(-direction.y(), direction.x())

        def at(distance: float, offset: float = 0.0) -> QPointF:
            return tip + direction * distance + normal * offset

        w = self.HALF_WIDTH
        # Maximum
        if cardinality.is_many:
            heel = at(self.FOOT_LENGTH)
            painter.drawLine(heel, at(0, w))
            painter.drawLine(heel, at(0, -w))
        else:
            painter.drawLine(at(self.MAX_BAR, -w), at(self.MAX_BAR, w))
        # Minimum
        if cardinality.is_optional:
            painter.drawEllipse(at(self.CIRCLE_CENTER), self.CIRCLE_RADIUS, self.CIRCLE_RADIUS)
        else:
            painter.drawLine(at(self.MIN_BAR, -w), at(self.MIN_BAR, w))
