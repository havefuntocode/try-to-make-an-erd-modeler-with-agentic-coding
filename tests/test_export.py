"""Tests für Drucken, PDF- und PNG-Export."""

import pytest
from PySide6.QtCore import QRectF, QSizeF
from PySide6.QtGui import QColor, QImage, QPageLayout

from erd_editor.diagram.scene import DiagramScene
from erd_editor.export import Orientation, PageFormat, PageSpec, export_pdf, export_png, print_scene
from erd_editor.export.render import (
    MAX_PNG_SIDE,
    auto_orientation,
    diagram_rect,
    export_mode,
    fit_rect,
    page_layout,
    png_size,
)
from erd_editor.model import TableRef


@pytest.fixture
def scene(qapp, test_schema):
    s = DiagramScene()
    s.load(test_schema)
    return s


def test_fit_rect_keeps_aspect_and_centers():
    r = fit_rect(QSizeF(200, 100), QRectF(0, 0, 400, 400))
    assert r == QRectF(0, 100, 400, 200)
    r = fit_rect(QSizeF(100, 200), QRectF(10, 10, 400, 400))
    assert r == QRectF(110, 10, 200, 400)


def test_export_mode_restores_selection_and_background(scene):
    item = scene.entities[TableRef("shop", None, "kunde")]
    item.setSelected(True)
    background = scene.backgroundBrush().color()
    with export_mode(scene):
        assert not scene.selectedItems()
        assert scene.backgroundBrush().color() == QColor("#FFFFFF")
    assert item.isSelected()
    assert scene.backgroundBrush().color() == background


class TestPng:
    def test_file_and_size(self, scene, tmp_path):
        path = tmp_path / "d.png"
        size = export_png(scene, str(path), 2.0)
        r = diagram_rect(scene)
        assert size.width() == round(r.width() * 2)
        image = QImage(str(path))
        assert image.size() == size
        assert image.pixelColor(2, 2) == QColor("#FFFFFF")  # weißer Rand

    def test_dpi_metadata(self, scene, tmp_path):
        path = tmp_path / "d.png"
        export_png(scene, str(path), 3.0)
        dpi = QImage(str(path)).dotsPerMeterX() * 0.0254
        assert dpi == pytest.approx(288, abs=1)

    def test_size_is_capped(self, scene):
        size = png_size(scene, 1000)
        assert max(size.width(), size.height()) <= MAX_PNG_SIDE

    def test_not_blank(self, scene, tmp_path):
        path = tmp_path / "d.png"
        export_png(scene, str(path), 1.0)
        image = QImage(str(path))
        colors = {image.pixel(x, y) for x in range(0, image.width(), 7) for y in range(0, image.height(), 7)}
        assert len(colors) > 5


class TestPdf:
    def test_writes_pdf(self, scene, tmp_path):
        path = tmp_path / "d.pdf"
        export_pdf(scene, str(path), PageSpec(PageFormat.A3, Orientation.LANDSCAPE), "Test", "Fußzeile")
        data = path.read_bytes()
        assert data.startswith(b"%PDF")
        assert len(data) > 5000

    def test_page_layouts(self, scene):
        assert page_layout(scene, PageSpec(PageFormat.A4, Orientation.PORTRAIT)).orientation() == QPageLayout.Orientation.Portrait
        assert page_layout(scene, PageSpec(PageFormat.A4, Orientation.LANDSCAPE)).orientation() == QPageLayout.Orientation.Landscape
        assert page_layout(scene, PageSpec()).orientation() == auto_orientation(scene)

    def test_auto_orientation_follows_diagram(self, scene):
        r = diagram_rect(scene)
        expected = QPageLayout.Orientation.Landscape if r.width() >= r.height() else QPageLayout.Orientation.Portrait
        assert auto_orientation(scene) == expected

    def test_print_to_pdf_printer(self, scene, tmp_path):
        from PySide6.QtPrintSupport import QPrinter

        path = tmp_path / "druck.pdf"
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(path))
        print_scene(scene, printer, "Fußzeile")
        assert path.read_bytes().startswith(b"%PDF")

    def test_text_fits_at_high_resolution(self, qapp):
        """Schriften in Pixeln: Breite skaliert mit dem Gerät wie die Geometrie."""
        from PySide6.QtGui import QFontMetricsF, QPainter

        from erd_editor.diagram.style import scene_font

        font = scene_font(9)
        assert font.pixelSize() == 12
        image = QImage(100, 100, QImage.Format.Format_ARGB32)
        image.setDotsPerMeterX(round(300 / 0.0254))
        image.setDotsPerMeterY(round(300 / 0.0254))
        painter = QPainter(image)
        painter.setFont(font)
        on_300dpi = painter.fontMetrics().horizontalAdvance("kunde_id")
        painter.end()
        assert on_300dpi == pytest.approx(QFontMetricsF(font).horizontalAdvance("kunde_id"), abs=2)


class TestMainWindowExport:
    @pytest.fixture
    def window(self, qapp, test_schema):
        from erd_editor.main import MainWindow

        w = MainWindow()
        w.load_diagram(test_schema)
        yield w
        w.setWindowModified(False)
        w.close()

    def test_export_menu_actions_exist(self, window):
        texts = [a.text().replace("&", "") for a in window.menuBar().actions()[0].menu().actions()]
        assert any("Drucken" in t for t in texts)
        assert any("PDF" in t for t in texts)
        assert any("PNG" in t for t in texts)

    def test_png_dialog_flow(self, window, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QFileDialog, QInputDialog

        from erd_editor.main import PNG_RESOLUTIONS

        target = tmp_path / "bild"
        monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: (list(PNG_RESOLUTIONS)[1], True))
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
        window.export_png_dialog()
        assert (tmp_path / "bild.png").exists()
        assert "PNG gespeichert" in window.statusBar().currentMessage()

    def test_pdf_dialog_flow(self, window, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QFileDialog, QInputDialog

        target = tmp_path / "plan.pdf"
        monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("A3 quer", True))
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
        window.export_pdf_dialog()
        assert target.read_bytes().startswith(b"%PDF")

    def test_empty_diagram_is_not_exported(self, qapp):
        from erd_editor.main import MainWindow

        w = MainWindow()
        w.export_png_dialog()
        assert "Nichts zu" in w.statusBar().currentMessage()
        w.close()

    def test_footer_and_export_path(self, window, tmp_path):
        from pathlib import Path

        window.path = Path(tmp_path / "shop.erd.json")
        assert window.footer_text().startswith("shop.erd.json · ")
        assert window._export_path(".pdf").name == "shop.pdf"
