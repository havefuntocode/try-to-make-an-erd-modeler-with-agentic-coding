"""Rechtwinklige Linienführung für Beziehungen.

Grundidee:
- Das Kind-Ende dockt an der Zeile der (ersten) FK-Spalte an.
- Das Eltern-Ende dockt an der Seite der Elterntabelle an, möglichst auf
  gleicher Höhe (gerade Linie); mehrere Enden auf derselben Seite werden
  so verteilt, dass sich die Symbole nicht überlagern.
- Liegen die Boxen weit genug nebeneinander, verbindet eine Z-Linie die
  einander zugewandten Seiten, sonst eine U-Linie über die rechten Seiten
  (auch für Selbstreferenzen).
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QPointF, QRectF


class Side(Enum):
    LEFT = -1
    RIGHT = 1

    @property
    def direction(self) -> QPointF:
        """Einheitsvektor von der Box weg."""
        return QPointF(float(self.value), 0.0)

    def x_of(self, rect: QRectF) -> float:
        return rect.left() if self is Side.LEFT else rect.right()


def choose_sides(child: QRectF, parent: QRectF, min_gap: float, same_item: bool = False) -> tuple[Side, Side]:
    """Seiten für Kind- und Eltern-Ende wählen.

    Z-Linie nur, wenn zwischen den Boxen mindestens `min_gap` Platz ist,
    damit beide Symbole auf geraden Stücken stehen.
    """
    if not same_item:
        if child.right() + min_gap <= parent.left():
            return Side.RIGHT, Side.LEFT
        if parent.right() + min_gap <= child.left():
            return Side.LEFT, Side.RIGHT
    return Side.RIGHT, Side.RIGHT


def route(start: QPointF, start_side: Side, end: QPointF, end_side: Side, stub: float, offset: float = 0.0) -> list[QPointF]:
    """Eckpunkte einer rechtwinkligen Linie von `start` nach `end`.

    `stub` ist die Mindestlänge des ersten/letzten Segments, `offset`
    verschiebt das senkrechte Mittelstück (für parallele Beziehungen).
    """
    if start_side is not end_side:
        mid_x = (start.x() + end.x()) / 2 + offset
        if abs(start.y() - end.y()) < 0.5:
            return [start, end]
        return [start, QPointF(mid_x, start.y()), QPointF(mid_x, end.y()), end]

    # U-Linie: beide Enden auf derselben Seite
    if start_side is Side.RIGHT:
        out_x = max(start.x(), end.x()) + stub + offset
    else:
        out_x = min(start.x(), end.x()) - stub - offset
    return [start, QPointF(out_x, start.y()), QPointF(out_x, end.y()), end]


def place(desired: list[float], fixed: list[float], low: float, high: float, spacing: float) -> list[float]:
    """Positionen auf einer Seite verteilen.

    Jede gewünschte Position wird auf den nächstgelegenen freien Platz
    gelegt, der mindestens `spacing` von festen und bereits vergebenen
    Positionen entfernt ist und in [low, high] liegt. Ist kein Platz frei,
    bleibt der Wunsch (begrenzt auf den Bereich) stehen.
    Reihenfolge des Ergebnisses = Reihenfolge von `desired`.
    """
    result: list[float | None] = [None] * len(desired)
    taken = list(fixed)
    for i in sorted(range(len(desired)), key=lambda k: desired[k]):
        want = min(max(desired[i], low), high)
        candidates = [want] + [o + d for o in taken for d in (-spacing, spacing)]
        free = [
            c
            for c in candidates
            if low <= c <= high and all(abs(c - o) >= spacing - 1e-6 for o in taken)
        ]
        chosen = min(free, key=lambda c: abs(c - want)) if free else want
        result[i] = chosen
        taken.append(chosen)
    return [r for r in result if r is not None]
