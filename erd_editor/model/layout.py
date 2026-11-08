"""Layout-Daten eines Diagramms – getrennt vom Schema gespeichert.

Enthält nur, was der Nutzer gestaltet (Positionen, Farben, Rahmen,
Darstellungsgröße), nichts, was sich aus der Datenbank ergibt.
Keine Qt-Abhängigkeit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from erd_editor.model.schema import TableRef

Frame = tuple[float, float, float, float]  # x, y, Breite, Höhe

MIN_SCALE = 0.5
MAX_SCALE = 3.0


@dataclass
class Layout:
    positions: dict[TableRef, tuple[float, float]] = field(default_factory=dict)  # linke obere Ecke
    database_colors: dict[str, int] = field(default_factory=dict)  # Datenbankname → Palettenindex
    frames: dict[str, Frame] = field(default_factory=dict)  # von Hand aufgezogene Rahmen
    schema_frames: dict[tuple[str, str], Frame] = field(default_factory=dict)  # (Datenbank, Schema) → Rahmen
    scale: float = 1.0  # Darstellungsgröße (1.0 = 100 %)
    notation: str = "crowsfoot"
