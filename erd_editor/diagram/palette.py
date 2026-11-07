"""Farbpaletten für Datenbank-Behälter.

Druckfreundlich: sehr helle Füllung, kräftiger Rand. Die Kopfzeilen der
Entitäten übernehmen den Farbton ihrer Datenbank.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor


@dataclass(frozen=True)
class ContainerColors:
    name: str
    fill: str  # Fläche des Behälters
    band: str  # Kopfzeile des Behälters
    entity_header: str  # Kopfzeile der Entitäten darin
    border: str  # Rand von Behälter und Entitäten

    def qcolor(self, attr: str) -> QColor:
        return QColor(getattr(self, attr))


PALETTE: tuple[ContainerColors, ...] = (
    ContainerColors("Blau", "#F2F6FC", "#DCE6F4", "#DCE6F2", "#4A74B0"),
    ContainerColors("Grün", "#F1F8F2", "#D9EEDD", "#DAEEDD", "#4C8F5A"),
    ContainerColors("Orange", "#FDF6EE", "#F8E3CB", "#F7E2CA", "#B9732C"),
    ContainerColors("Violett", "#F6F2FA", "#E6DCF2", "#E5DBF1", "#7D5BA6"),
    ContainerColors("Petrol", "#EFF7F7", "#D3EBEA", "#D2EAE9", "#2F8582"),
    ContainerColors("Rosé", "#FCF2F4", "#F5DCE2", "#F4DBE1", "#B04E66"),
)


def colors_for(index: int) -> ContainerColors:
    """Farben für die n-te Datenbank (wiederholt sich nach sechs)."""
    return PALETTE[index % len(PALETTE)]
