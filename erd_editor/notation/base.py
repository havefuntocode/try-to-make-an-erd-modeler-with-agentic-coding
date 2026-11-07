"""Gemeinsame Basis aller Notationen (Linienenden einer Beziehung).

Eine Notation zeichnet nur das Ende einer Linie an einer Entität.
Die Linie selbst, ihre Führung und ihr Stil (durchgezogen/gestrichelt)
gehören zur Darstellung in `erd_editor.diagram`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainter

from erd_editor.analysis import Cardinality


class Notation(ABC):
    """Basisklasse; Unterklassen: Krähenfuß, später UML, (min,max), IDEF1X."""

    name: str

    @abstractmethod
    def end_length(self, cardinality: Cardinality) -> float:
        """Länge, die das Symbol entlang der Linie (ab Entitätsrand) braucht.

        Die Linienführung sorgt dafür, dass das erste und letzte Segment
        mindestens so lang sind.
        """

    @abstractmethod
    def end_width(self) -> float:
        """Halbe Breite des Symbols quer zur Linie (für Abstände und Umriss)."""

    @abstractmethod
    def draw_end(self, painter: QPainter, tip: QPointF, direction: QPointF, cardinality: Cardinality) -> None:
        """Ende zeichnen.

        `tip` liegt auf dem Rand der Entität, `direction` ist der Einheitsvektor
        von der Entität weg entlang der Linie. Stift und Pinsel sind gesetzt.
        """
