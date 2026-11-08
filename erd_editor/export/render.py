"""Drucken, PDF-, PNG- und SVG-Export der Diagrammszene.

Alle Ziele nutzen dieselbe Zeichenroutine: weißer Hintergrund, ohne
Auswahl-Hervorhebung, Diagramm seitenfüllend mit Rand. Vektorausgabe
(PDF, Drucker) bleibt bei jeder Größe scharf.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import QMarginsF, QRectF, QSize, QSizeF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPageLayout, QPageSize, QPainter, QPdfWriter
from PySide6.QtSvg import QSvgGenerator
from PySide6.QtWidgets import QGraphicsScene

MARGIN = 24.0  # Rand um das Diagramm in Szeneneinheiten
MAX_PNG_SIDE = 16000  # Obergrenze je Bildseite (Speicher)
WHITE = QColor("#FFFFFF")


class PageFormat(Enum):
    A4 = QPageSize.PageSizeId.A4
    A3 = QPageSize.PageSizeId.A3


class Orientation(Enum):
    AUTO = "auto"
    PORTRAIT = "hoch"
    LANDSCAPE = "quer"


@dataclass(frozen=True)
class PageSpec:
    format: PageFormat = PageFormat.A4
    orientation: Orientation = Orientation.AUTO
    margin_mm: float = 10.0


# ---------------------------------------------------------------------- Grundlagen


@contextmanager
def export_mode(scene: QGraphicsScene) -> Iterator[None]:
    """Auswahl und grauen Hintergrund für die Dauer des Exports ausblenden."""
    selected = scene.selectedItems()
    background = scene.backgroundBrush()
    scene.clearSelection()
    scene.setBackgroundBrush(WHITE)
    try:
        yield
    finally:
        scene.setBackgroundBrush(background)
        for item in selected:
            item.setSelected(True)


def diagram_rect(scene: QGraphicsScene) -> QRectF:
    """Bereich des Diagramms samt Rand (Szenenkoordinaten)."""
    return scene.itemsBoundingRect().adjusted(-MARGIN, -MARGIN, MARGIN, MARGIN)


def fit_rect(source: QSizeF, target: QRectF) -> QRectF:
    """Größtes Rechteck mit dem Seitenverhältnis von `source`, zentriert in `target`."""
    if source.width() <= 0 or source.height() <= 0:
        return QRectF(target)
    factor = min(target.width() / source.width(), target.height() / source.height())
    w, h = source.width() * factor, source.height() * factor
    return QRectF(target.x() + (target.width() - w) / 2, target.y() + (target.height() - h) / 2, w, h)


def auto_orientation(scene: QGraphicsScene) -> QPageLayout.Orientation:
    r = diagram_rect(scene)
    return QPageLayout.Orientation.Landscape if r.width() >= r.height() else QPageLayout.Orientation.Portrait


def _qt_orientation(scene: QGraphicsScene, orientation: Orientation) -> QPageLayout.Orientation:
    if orientation is Orientation.PORTRAIT:
        return QPageLayout.Orientation.Portrait
    if orientation is Orientation.LANDSCAPE:
        return QPageLayout.Orientation.Landscape
    return auto_orientation(scene)


def paint_page(painter: QPainter, page: QRectF, scene: QGraphicsScene, footer: str | None = None) -> QRectF:
    """Diagramm seitenfüllend in `page` zeichnen, darunter optional eine Fußzeile.

    Liefert das tatsächlich bemalte Rechteck des Diagramms.
    """
    painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
    area = QRectF(page)
    if footer:
        font = QFont("Segoe UI")
        # Schriftgröße relativ zur Seite, damit sie bei jeder Auflösung gleich wirkt
        font.setPixelSize(max(8, round(page.height() * 0.012)))
        fm = QFontMetricsF(font)
        footer_h = fm.height() * 1.6
        area.setBottom(area.bottom() - footer_h)
        painter.save()
        painter.setFont(font)
        painter.setPen(QColor("#6B7280"))
        painter.drawText(
            QRectF(page.left(), page.bottom() - footer_h, page.width(), footer_h),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
            footer,
        )
        painter.restore()
    source = diagram_rect(scene)
    target = fit_rect(source.size(), area)
    with export_mode(scene):
        scene.render(painter, target, source, Qt.AspectRatioMode.KeepAspectRatio)
    return target


# ---------------------------------------------------------------------- PNG


def png_size(scene: QGraphicsScene, factor: float) -> QSize:
    """Bildgröße in Pixeln; begrenzt auf MAX_PNG_SIDE je Seite."""
    r = diagram_rect(scene)
    factor = min(factor, MAX_PNG_SIDE / max(r.width(), r.height(), 1))
    return QSize(max(1, round(r.width() * factor)), max(1, round(r.height() * factor)))


def export_png(scene: QGraphicsScene, path: str, factor: float = 2.0) -> QSize:
    """Diagramm als PNG speichern; `factor` 2 = doppelte Bildschirmauflösung."""
    size = png_size(scene, factor)
    image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(WHITE)
    # DPI-Angabe in der Datei, damit Textverarbeitungen die Größe richtig übernehmen
    dots_per_meter = round(96 * factor / 0.0254)
    image.setDotsPerMeterX(dots_per_meter)
    image.setDotsPerMeterY(dots_per_meter)
    painter = QPainter(image)
    try:
        paint_page(painter, QRectF(0, 0, size.width(), size.height()), scene)
    finally:
        painter.end()
    if not image.save(path, "PNG"):
        raise OSError(f"PNG konnte nicht gespeichert werden: {path}")
    return size


# ---------------------------------------------------------------------- SVG


def export_svg(scene: QGraphicsScene, path: str, title: str = "") -> QSize:
    """Diagramm als SVG speichern (Vektorgrafik mit echtem Text).

    Eine Szeneneinheit entspricht einem SVG-Pixel; Programme wie Word,
    LibreOffice oder Inkscape können das Bild verlustfrei skalieren.
    """
    source = diagram_rect(scene)
    size = QSize(max(1, round(source.width())), max(1, round(source.height())))
    generator = QSvgGenerator()
    generator.setFileName(path)
    generator.setSize(size)
    generator.setViewBox(QRectF(0, 0, size.width(), size.height()))
    generator.setResolution(96)
    generator.setTitle(title or "ER-Diagramm")
    generator.setDescription("Erstellt mit dem ER-Diagramm-Editor")
    painter = QPainter(generator)
    if not painter.isActive():
        raise OSError(f"SVG konnte nicht geschrieben werden: {path}")
    try:
        painter.fillRect(QRectF(0, 0, size.width(), size.height()), WHITE)
        paint_page(painter, QRectF(0, 0, size.width(), size.height()), scene)
    finally:
        painter.end()
    return size


# ---------------------------------------------------------------------- PDF und Druck


def page_layout(scene: QGraphicsScene, spec: PageSpec) -> QPageLayout:
    m = spec.margin_mm
    return QPageLayout(
        QPageSize(spec.format.value),
        _qt_orientation(scene, spec.orientation),
        QMarginsF(m, m, m, m),
        QPageLayout.Unit.Millimeter,
    )


def export_pdf(scene: QGraphicsScene, path: str, spec: PageSpec = PageSpec(), title: str = "", footer: str | None = None) -> None:
    """Diagramm als einseitiges PDF (Vektorgrafik) speichern."""
    writer = QPdfWriter(path)
    writer.setResolution(300)
    writer.setTitle(title or "ER-Diagramm")
    writer.setCreator("ER-Diagramm-Editor")
    writer.setPageLayout(page_layout(scene, spec))
    painter = QPainter(writer)
    if not painter.isActive():
        raise OSError(f"PDF konnte nicht geschrieben werden: {path}")
    try:
        page = QRectF(writer.pageLayout().paintRectPixels(writer.resolution()))
        page.moveTo(0, 0)  # der Painter beginnt im bedruckbaren Bereich
        paint_page(painter, page, scene, footer)
    finally:
        painter.end()


def print_scene(scene: QGraphicsScene, printer, footer: str | None = None) -> None:
    """Auf einen QPrinter zeichnen (Druck oder Druckvorschau)."""
    painter = QPainter(printer)
    if not painter.isActive():
        return
    try:
        page = QRectF(printer.pageLayout().paintRectPixels(printer.resolution()))
        page.moveTo(0, 0)
        paint_page(painter, page, scene, footer)
    finally:
        painter.end()
