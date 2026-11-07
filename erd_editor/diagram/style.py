"""Farben, Schriften und Maße der Diagrammelemente.

Druckfreundlich: helle Flächen, kräftige Ränder, dunkle Schrift.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtGui import QColor, QFont


def scene_font(points: float, bold: bool = False) -> QFont:
    """Schrift für die Szene in Pixeln statt Punkt.

    Punktgrößen rechnet Qt mit der Auflösung des Ausgabegeräts um
    (Bildschirm 96 dpi, PDF 300 dpi …); die Boxen sind aber in
    Szeneneinheiten vermessen. Pixelgrößen skalieren dagegen genau wie
    die Geometrie – Text passt so auf jedem Gerät in seine Box.
    `points` ist die gewünschte Größe bei 100 % auf einem 96-dpi-Bildschirm.
    """
    f = QFont("Segoe UI")
    f.setPixelSize(max(1, round(points * 96 / 72)))
    f.setBold(bold)
    return f


_font = scene_font


@dataclass
class EntityStyle:
    # Farben
    body: QColor = field(default_factory=lambda: QColor("#FFFFFF"))
    header: QColor = field(default_factory=lambda: QColor("#DCE6F2"))
    header_text: QColor = field(default_factory=lambda: QColor("#1F2A37"))
    border: QColor = field(default_factory=lambda: QColor("#5B7DB1"))
    selected_border: QColor = field(default_factory=lambda: QColor("#E07A00"))
    text: QColor = field(default_factory=lambda: QColor("#1F2A37"))
    type_text: QColor = field(default_factory=lambda: QColor("#6B7280"))
    index_area: QColor = field(default_factory=lambda: QColor("#F3F4F6"))
    index_text: QColor = field(default_factory=lambda: QColor("#4B5563"))
    separator: QColor = field(default_factory=lambda: QColor("#C7D2E0"))

    # Schriften
    header_font: QFont = field(default_factory=lambda: _font(10, bold=True))
    column_font: QFont = field(default_factory=lambda: _font(9))
    pk_font: QFont = field(default_factory=lambda: _font(9, bold=True))
    index_font: QFont = field(default_factory=lambda: _font(8))

    # Maße (Szeneneinheiten ≈ Pixel bei 100 %)
    radius: float = 6.0
    border_width: float = 1.2
    selected_border_width: float = 2.4
    padding: float = 8.0
    icon_size: float = 14.0
    marker_size: float = 9.0
    gap: float = 5.0
    type_gap: float = 18.0
    row_spacing: float = 3.0
    min_width: float = 140.0
