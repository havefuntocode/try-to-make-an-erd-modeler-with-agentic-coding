"""Tests für Darstellungsgröße und von Hand aufgezogene Rahmen."""

import pytest
from PySide6.QtCore import QPointF, QRectF

from erd_editor.diagram.container_item import Edge
from erd_editor.diagram.routing import path_hits
from erd_editor.diagram.scene import DiagramScene
from erd_editor.model import Layout, TableRef
from erd_editor.persistence.document import DiagramDocument, document_from_dict, document_to_dict


def shop(table: str) -> TableRef:
    return TableRef("shop", None, table)


@pytest.fixture
def scene(qapp, test_schema):
    s = DiagramScene()
    s.load(test_schema)
    return s


def boxes(scene):
    return [item.mapRectToScene(item.rect) for item in scene.entities.values()]


def assert_no_overlaps(rects):
    for i, a in enumerate(rects):
        for b in rects[i + 1 :]:
            assert not a.intersects(b)


# ---------------------------------------------------------------- Darstellungsgröße


class TestDisplayScale:
    def test_boxes_grow(self, scene):
        before = scene.entities[shop("kunde")].mapRectToScene(scene.entities[shop("kunde")].rect)
        scene.set_display_scale(1.5)
        after = scene.entities[shop("kunde")].mapRectToScene(scene.entities[shop("kunde")].rect)
        assert after.width() == pytest.approx(before.width() * 1.5)
        assert after.height() == pytest.approx(before.height() * 1.5)

    def test_no_overlaps_after_scaling(self, scene):
        scene.set_display_scale(2.0)
        assert_no_overlaps(boxes(scene))

    def test_containers_still_enclose_members(self, scene):
        scene.set_display_scale(1.75)
        for c in scene.containers.values():
            for item in c.members:
                assert c.rect.contains(item.mapRectToScene(item.rect))

    def test_auto_layout_at_large_scale(self, scene):
        scene.set_display_scale(2.0)
        scene.auto_layout()
        assert_no_overlaps(boxes(scene))
        rects = {id(i): i.mapRectToScene(i.rect) for i in scene.entities.values()}
        for r in scene.relations:
            others = [v for k, v in rects.items() if k not in (id(r.child_item), id(r.parent_item))]
            assert not path_hits(r.points, others, clearance=0), r.relationship.name

    def test_text_grows_relative_to_diagram(self, scene):
        """Nach Auto-Layout wächst das Diagramm weniger als die Schrift."""
        width_100 = scene.itemsBoundingRect().width()
        scene.set_display_scale(2.0)
        scene.auto_layout()
        assert scene.itemsBoundingRect().width() < 2.0 * width_100

    def test_clamped(self, scene):
        scene.set_display_scale(99)
        assert scene.display_scale == 3.0
        scene.set_display_scale(0.01)
        assert scene.display_scale == 0.5

    def test_back_to_100_restores_positions(self, scene):
        before = {r: (i.pos().x(), i.pos().y()) for r, i in scene.entities.items()}
        scene.set_display_scale(1.5)
        scene.set_display_scale(1.0)
        for ref, (x, y) in before.items():
            assert scene.entities[ref].pos().x() == pytest.approx(x)
            assert scene.entities[ref].pos().y() == pytest.approx(y)

    def test_saved_and_restored(self, scene, test_schema):
        scene.set_display_scale(1.25)
        layout = scene.current_layout()
        assert layout.scale == 1.25
        restored = document_from_dict(document_to_dict(DiagramDocument(test_schema, layout))).layout
        other = DiagramScene()
        other.load(test_schema, restored)
        assert other.display_scale == 1.25
        assert other.entities[shop("kunde")].scale() == 1.25

    def test_emits_layout_changed(self, scene):
        calls = []
        scene.layout_changed.connect(lambda: calls.append(1))
        scene.set_display_scale(1.5)
        assert calls


# ---------------------------------------------------------------- Rahmen aufziehen


@pytest.fixture
def master(scene):
    return scene.containers["stammdaten"]


class TestFrames:
    def test_resize_grows(self, master):
        r = master.rect
        master.resize_to(r.adjusted(0, 0, 200, 100))
        assert master.rect.width() == pytest.approx(r.width() + 200)
        assert master.rect.height() == pytest.approx(r.height() + 100)

    def test_cannot_shrink_below_content(self, master):
        content = master.content_rect()
        master.resize_to(QRectF(content.x(), content.y(), 10, 10))
        assert master.rect.contains(content)

    def test_moving_container_moves_frame(self, master):
        master.resize_to(master.rect.adjusted(0, 0, 200, 100))
        before = master.rect
        master.move_by(30, 40)
        assert master.rect.topLeft() == before.topLeft() + QPointF(30, 40)
        assert master.rect.size() == before.size()

    def test_fit_to_content(self, scene, master):
        master.resize_to(master.rect.adjusted(0, 0, 200, 100))
        scene.fit_frames_to_content()
        assert master.manual_rect is None
        assert master.rect == master.content_rect()

    def test_auto_layout_resets_frames(self, scene, master):
        master.resize_to(master.rect.adjusted(0, 0, 200, 100))
        scene.auto_layout()
        assert master.manual_rect is None

    def test_edge_zones(self, master):
        r = master.rect
        assert master.edge_at(QPointF(r.right(), r.center().y())) == Edge.RIGHT
        assert master.edge_at(QPointF(r.left(), r.center().y())) == Edge.LEFT
        assert master.edge_at(QPointF(r.center().x(), r.bottom())) == Edge.BOTTOM
        assert master.edge_at(r.bottomRight()) == Edge.BOTTOM | Edge.RIGHT
        assert master.edge_at(r.bottomLeft()) == Edge.BOTTOM | Edge.LEFT
        assert master.edge_at(r.center()) == Edge.NONE
        assert master.edge_at(QPointF(r.right(), r.top() + 2)) == Edge.NONE  # Kopfzeile: verschieben

    def test_resized_helper(self, master):
        r = QRectF(0, 0, 100, 100)
        assert master._resized(r, Edge.RIGHT, 50, 999) == QRectF(0, 0, 150, 100)
        assert master._resized(r, Edge.LEFT, -20, 0) == QRectF(-20, 0, 120, 100)
        assert master._resized(r, Edge.BOTTOM | Edge.RIGHT, 10, 20) == QRectF(0, 0, 110, 120)

    def test_frame_saved_and_restored(self, scene, master, test_schema):
        master.resize_to(master.rect.adjusted(-50, 0, 200, 100))
        expected = master.rect
        layout = document_from_dict(document_to_dict(DiagramDocument(test_schema, scene.current_layout()))).layout
        assert "stammdaten" in layout.frames and "shop" not in layout.frames
        other = DiagramScene()
        other.load(test_schema, layout)
        got = other.containers["stammdaten"].rect
        for a, b in ((got.x(), expected.x()), (got.y(), expected.y()), (got.width(), expected.width())):
            assert a == pytest.approx(b, abs=0.1)

    def test_scaling_scales_frame(self, scene, master):
        master.resize_to(master.rect.adjusted(0, 0, 300, 0))
        width = master.rect.width()
        scene.set_display_scale(2.0)
        assert master.rect.width() == pytest.approx(width * 2, rel=0.02)

    def test_old_files_without_scale_or_frames(self):
        layout = document_from_dict(
            {"format": "erd-editor", "version": 1, "layout": {"databases": [{"name": "x", "color": 1}]}}
        ).layout
        assert layout == Layout(database_colors={"x": 1})


def test_main_window_scale_steps(qapp, test_schema):
    from erd_editor.main import DISPLAY_SCALES, MainWindow

    w = MainWindow()
    w.load_diagram(test_schema)
    w.step_display_scale(+1)
    assert w.scene.display_scale == DISPLAY_SCALES[1]
    assert w.scale_actions[DISPLAY_SCALES[1]].isChecked()
    for _ in range(10):
        w.step_display_scale(+1)
    assert w.scene.display_scale == DISPLAY_SCALES[-1]
    w.step_display_scale(-1)
    assert w.scene.display_scale == DISPLAY_SCALES[-2]
    w.setWindowModified(False)
    w.close()


def _mouse(view, kind, scene_pos, buttons):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    local = QPointF(view.mapFromScene(scene_pos))
    button = Qt.MouseButton.LeftButton if kind != QEvent.Type.MouseMove else Qt.MouseButton.NoButton
    event = QMouseEvent(kind, local, view.viewport().mapToGlobal(local), button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(view.viewport(), event)


def drag(view, start, end):
    from PySide6.QtCore import QEvent, Qt

    held, none = Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton
    _mouse(view, QEvent.Type.MouseButtonPress, start, held)
    _mouse(view, QEvent.Type.MouseMove, (start + end) / 2, held)
    _mouse(view, QEvent.Type.MouseMove, end, held)
    _mouse(view, QEvent.Type.MouseButtonRelease, end, none)


class TestMouse:
    @pytest.fixture
    def view(self, scene):
        from erd_editor.diagram.view import DiagramView

        v = DiagramView(scene)
        v.resize(1600, 1200)
        v.show()
        v.set_zoom(1.0)
        v.centerOn(scene.containers["stammdaten"].rect.center())
        yield v
        v.close()

    def test_drag_right_edge_resizes(self, view, master):
        r = master.rect
        start = QPointF(r.right() - 2, r.center().y())
        drag(view, start, start + QPointF(120, 0))
        assert master.rect.width() == pytest.approx(r.width() + 120, abs=3)

    def test_drag_header_moves(self, view, master):
        r = master.rect
        land = master.members[0]
        before = land.pos()
        start = QPointF(r.center().x(), r.top() + 10)
        drag(view, start, start + QPointF(60, 40))
        assert land.pos().x() == pytest.approx(before.x() + 60, abs=3)
        assert land.pos().y() == pytest.approx(before.y() + 40, abs=3)

    def test_click_free_area_does_not_move(self, view, master):
        r, content = master.rect, master.content_rect()
        before = master.members[0].pos()
        start = QPointF(r.left() + 6 + master.grip, r.bottom() - 6 - master.grip)
        assert master.edge_at(start) == Edge.NONE and not master.header_rect.contains(start)
        drag(view, start, start + QPointF(50, 0))
        assert master.members[0].pos() == before
        assert master.rect == content
