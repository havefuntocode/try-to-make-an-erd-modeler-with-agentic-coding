"""(min,max)-Notation (ISO/IEC 11179, „Schlee/Abrial“).

An jedem Linienende steht, wie oft *diese* Entität an der Beziehung
teilnimmt: Eine Bestellung gehört zu genau einem Kunden → „(1,1)“ an
der Bestellung; ein Kunde hat beliebig viele Bestellungen → „(0,n)“
am Kunden. Die Leserichtung ist damit umgekehrt zum Krähenfuß – daher
wird die Kardinalität der Gegenseite (`opposite`) beschriftet.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QFont, QFontMetricsF, QPainter

from erd_editor.analysis import Cardinality
from erd_editor.notation.base import Notation


def _label_font() -> QFont:
    font = QFont("Segoe UI")
    font.setPixelSize(11)  # Pixel statt Punkt: gleiche Größe auf Bildschirm, PDF und SVG
    return font


def minmax_label(participation: Cardinality) -> str:
    """`(0,n)`, `(1,1)` …"""
    upper = "n" if participation.maximum is None else str(participation.maximum)
    return f"({participation.minimum},{upper})"


class MinMaxNotation(Notation):
    key = "minmax"
    name = "(min,max)"

    GAP = 4.0  # Abstand des Textes vom Entitätsrand und von der Linie

    def __init__(self) -> None:
        self.font = _label_font()
        self._fm = QFontMetricsF(self.font)

    def end_length(self, cardinality: Cardinality) -> float:
        return self._fm.horizontalAdvance("(0,n)") + 2 * self.GAP + 6

    def end_width(self) -> float:
        return self._fm.height() / 2 + self.GAP

    def draw_end(
        self,
        painter: QPainter,
        tip: QPointF,
        direction: QPointF,
        cardinality: Cardinality,
        opposite: Cardinality | None = None,
        at_child: bool = True,
    ) -> None:
        text = minmax_label(opposite if opposite is not None else cardinality)
        width, height = self._fm.horizontalAdvance(text), self._fm.height()
        # Text oberhalb der Linie, auf der vom Rand abgewandten Seite
        if direction.x() >= 0:
            left = tip.x() + self.GAP
        else:
            left = tip.x() - self.GAP - width
        rect = QRectF(left, tip.y() - self.GAP / 2 - height, width, height)
        painter.save()
        painter.setFont(self.font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()
