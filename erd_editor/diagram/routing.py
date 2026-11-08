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


# ---------------------------------------------------------------------- Hindernisse


def _segment_hits(a: QPointF, b: QPointF, obstacles: list[QRectF], clearance: float) -> bool:
    """True, wenn das waagerechte/senkrechte Segment a–b eine Box schneidet."""
    seg = QRectF(a, b).normalized().adjusted(-0.5, -0.5, 0.5, 0.5)
    return any(seg.intersects(r.adjusted(-clearance, -clearance, clearance, clearance)) for r in obstacles)


def path_hits(points: list[QPointF], obstacles: list[QRectF], clearance: float = 2.0) -> bool:
    return any(_segment_hits(a, b, obstacles, clearance) for a, b in zip(points, points[1:]))


def route_avoiding(
    start: QPointF,
    start_side: Side,
    end: QPointF,
    end_side: Side,
    stub: float,
    offset: float,
    obstacles: list[QRectF],
    channel_gap: float = 14.0,
) -> list[QPointF]:
    """Wie `route`, weicht aber anderen Boxen aus (soweit einfach möglich).

    1. Einfache Linie wie `route`.
    2. Z-Linie mit dem senkrechten Stück direkt neben Kind oder Elternteil.
    3. Linie mit Kanal: neben dem Kind senkrecht zu einer freien Höhe,
       waagerecht durch die Lücke zwischen den Boxen, neben dem Elternteil
       wieder senkrecht zum Andockpunkt.
    Findet sich kein freier Weg, bleibt es bei der einfachen Linie.
    """
    simple = route(start, start_side, end, end_side, stub, offset)
    if not obstacles or not path_hits(simple, obstacles):
        return simple

    x1 = start.x() + start_side.value * (stub + abs(offset))
    x2 = end.x() + end_side.value * (stub + abs(offset))

    if start_side is not end_side:
        for mid_x in (x1, x2):
            candidate = [start, QPointF(mid_x, start.y()), QPointF(mid_x, end.y()), end]
            if not path_hits(candidate, obstacles):
                return candidate
    else:
        # U-Linie: senkrechtes Stück neben die Boxen im Weg legen
        lo_y, hi_y = min(start.y(), end.y()), max(start.y(), end.y())
        d = start_side.value
        base = max(start.x(), end.x()) if d > 0 else min(start.x(), end.x())
        blocking = [r for r in obstacles if r.bottom() >= lo_y and r.top() <= hi_y]
        outs = sorted(
            {(r.right() if d > 0 else r.left()) + d * (channel_gap + abs(offset)) for r in blocking},
            key=lambda x: abs(x - base),
        )
        for out_x in outs:
            if (out_x - base) * d < stub:  # Symbole brauchen ihr gerades Stück
                continue
            candidate = [start, QPointF(out_x, start.y()), QPointF(out_x, end.y()), end]
            if not path_hits(candidate, obstacles):
                return candidate

    # Kanal suchen: freie Höhen ober- und unterhalb der Boxen im Weg
    lo_x, hi_x = min(x1, x2), max(x1, x2)
    in_range = [r for r in obstacles if r.right() >= lo_x and r.left() <= hi_x]
    candidates = {start.y(), end.y()}
    for r in in_range:
        candidates.add(r.top() - channel_gap + offset / 2)
        candidates.add(r.bottom() + channel_gap + offset / 2)
    target = (start.y() + end.y()) / 2
    for y in sorted(candidates, key=lambda c: abs(c - target)):
        candidate = [
            start,
            QPointF(x1, start.y()),
            QPointF(x1, y),
            QPointF(x2, y),
            QPointF(x2, end.y()),
            end,
        ]
        if not path_hits(candidate, obstacles):
            return _drop_redundant(candidate)
    return simple


def _drop_redundant(points: list[QPointF]) -> list[QPointF]:
    """Doppelte Punkte und Punkte auf einer Geraden entfernen."""
    result: list[QPointF] = []
    for p in points:
        if result and abs(result[-1].x() - p.x()) < 1e-6 and abs(result[-1].y() - p.y()) < 1e-6:
            continue
        if len(result) >= 2:
            a, b = result[-2], result[-1]
            if (abs(a.x() - b.x()) < 1e-6 and abs(b.x() - p.x()) < 1e-6) or (
                abs(a.y() - b.y()) < 1e-6 and abs(b.y() - p.y()) < 1e-6
            ):
                result[-1] = p
                continue
        result.append(p)
    return result
