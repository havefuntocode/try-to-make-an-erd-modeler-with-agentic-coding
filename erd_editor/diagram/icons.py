"""SVG-Icons laden und vektoriell zeichnen (scharf bei jedem Zoom und im PDF)."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from PySide6.QtCore import QRectF
from PySide6.QtGui import QPainter
from PySide6.QtSvg import QSvgRenderer

ICON_DIR = Path(__file__).resolve().parent.parent / "resources" / "icons"


@cache
def renderer(name: str) -> QSvgRenderer:
    """Renderer für `resources/icons/<name>.svg` (zwischengespeichert)."""
    path = ICON_DIR / f"{name}.svg"
    r = QSvgRenderer(str(path))
    if not r.isValid():
        raise FileNotFoundError(f"Icon fehlt oder ist ungültig: {path}")
    return r


def draw_icon(painter: QPainter, name: str, rect: QRectF) -> None:
    renderer(name).render(painter, rect)
