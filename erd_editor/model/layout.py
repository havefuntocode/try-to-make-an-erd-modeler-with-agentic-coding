"""Layout-Daten eines Diagramms – getrennt vom Schema gespeichert.

Enthält nur, was der Nutzer gestaltet (Positionen, Farben), nichts,
was sich aus der Datenbank ergibt. Keine Qt-Abhängigkeit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from erd_editor.model.schema import TableRef


@dataclass
class Layout:
    positions: dict[TableRef, tuple[float, float]] = field(default_factory=dict)  # linke obere Ecke
    database_colors: dict[str, int] = field(default_factory=dict)  # Datenbankname → Palettenindex
    notation: str = "crowsfoot"
