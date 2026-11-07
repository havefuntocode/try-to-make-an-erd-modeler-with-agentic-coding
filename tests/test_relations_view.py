"""Tests für Notation, Linienführung und Beziehungslinien."""

import pytest
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QImage, QPainter

from erd_editor.analysis import EXACTLY_ONE, ZERO_OR_MANY, ZERO_OR_ONE
from erd_editor.diagram.routing import Side, choose_sides, place, route
from erd_editor.diagram.scene import DiagramScene
from erd_editor.model import TableRef
from erd_editor.notation import CrowsFootNotation


def shop(table: str) -> TableRef:
    return TableRef("shop", None, table)


# ---------------------------------------------------------------- Routing (reine Geometrie)


class TestChooseSides:
    def test_parent_right_of_child(self):
        assert choose_sides(QRectF(0, 0, 100, 50), QRectF(300, 0, 100, 50), 60) == (Side.RIGHT, Side.LEFT)

    def test_parent_left_of_child(self):
        assert choose_sides(QRectF(300, 0, 100, 50), QRectF(0, 0, 100, 50), 60) == (Side.LEFT, Side.RIGHT)

    def test_too_close_uses_same_side(self):
        assert choose_sides(QRectF(0, 0, 100, 50), QRectF(130, 0, 100, 50), 60) == (Side.RIGHT, Side.RIGHT)

    def test_stacked_uses_same_side(self):
        assert choose_sides(QRectF(0, 0, 100, 50), QRectF(0, 200, 100, 50), 60) == (Side.RIGHT, Side.RIGHT)

    def test_self_reference(self):
        r = QRectF(0, 0, 100, 50)
        assert choose_sides(r, r, 60, same_item=True) == (Side.RIGHT, Side.RIGHT)


class TestRoute:
    def test_straight_line_when_level(self):
        pts = route(QPointF(100, 20), Side.RIGHT, QPointF(300, 20), Side.LEFT, 30)
        assert pts == [QPointF(100, 20), QPointF(300, 20)]

    def test_z_route_is_orthogonal(self):
        pts = route(QPointF(100, 20), Side.RIGHT, QPointF(300, 80), Side.LEFT, 30)
        assert len(pts) == 4
        for a, b in zip(pts, pts[1:]):
            assert a.x() == b.x() or a.y() == b.y()
        assert pts[1].x() == 200

    def test_offset_moves_middle(self):
        pts = route(QPointF(100, 20), Side.RIGHT, QPointF(300, 80), Side.LEFT, 30, offset=12)
        assert pts[1].x() == 212

    def test_u_route_right(self):
        pts = route(QPointF(100, 20), Side.RIGHT, QPointF(120, 200), Side.RIGHT, 30)
        assert pts[1].x() == 150 and pts[2].x() == 150
        assert pts[0] == QPointF(100, 20) and pts[-1] == QPointF(120, 200)


class TestPlace:
    def test_free_positions_stay(self):
        assert place([10, 50], [], 0, 100, 16) == [10, 50]

    def test_conflicts_are_spread(self):
        ys = place([50, 50, 50], [], 0, 100, 16)
        ys_sorted = sorted(ys)
        assert all(b - a >= 16 - 1e-6 for a, b in zip(ys_sorted, ys_sorted[1:]))

    def test_fixed_positions_are_avoided(self):
        (y,) = place([50], [50], 0, 100, 16)
        assert abs(y - 50) >= 16 - 1e-6

    def test_clamped_to_bounds(self):
        assert place([500], [], 0, 100, 16) == [100]

    def test_result_keeps_input_order(self):
        ys = place([80, 10], [], 0, 100, 16)
        assert ys == [80, 10]


# ---------------------------------------------------------------- Notation


@pytest.mark.parametrize("card", [ZERO_OR_ONE, EXACTLY_ONE, ZERO_OR_MANY])
def test_crowsfoot_draws_all_cardinalities(qapp, card):
    n = CrowsFootNotation()
    image = QImage(100, 100, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    for direction in (QPointF(1, 0), QPointF(-1, 0)):
        n.draw_end(painter, QPointF(50, 50), direction, card)
    painter.end()
    assert n.end_length(card) > n.CIRCLE_CENTER
    # es wurde tatsächlich etwas gezeichnet
    assert any(image.pixel(x, y) != 0 for x in range(100) for y in range(40, 60))


# ---------------------------------------------------------------- Beziehungslinien in der Szene


@pytest.fixture
def scene(qapp, test_schema):
    s = DiagramScene()
    s.load(test_schema)
    return s


def rel(scene, name):
    return next(r for r in scene.relations if r.relationship.name == name)


class TestRelationItems:
    def test_one_line_per_relationship(self, scene):
        assert len(scene.relations) == 11

    def test_lines_end_on_entity_edges(self, scene):
        for r in scene.relations:
            c = r.child_item.mapRectToScene(r.child_item.rect)
            p = r.parent_item.mapRectToScene(r.parent_item.rect)
            start, end = r.points[0], r.points[-1]
            assert start.x() == pytest.approx(r.child_side.x_of(c))
            assert c.top() <= start.y() <= c.bottom()
            assert end.x() == pytest.approx(r.parent_side.x_of(p))
            assert p.top() <= end.y() <= p.bottom()

    def test_child_end_at_fk_column(self, scene):
        r = rel(scene, "fk_bestellung_kunde")
        item = r.child_item
        expected = item.mapToScene(QPointF(0, item.column_anchor_y("kunde_id"))).y()
        assert r.points[0].y() == pytest.approx(expected)

    def test_lines_are_orthogonal(self, scene):
        for r in scene.relations:
            for a, b in zip(r.points, r.points[1:]):
                assert a.x() == pytest.approx(b.x()) or a.y() == pytest.approx(b.y())

    def test_self_reference_is_a_loop(self, scene):
        r = rel(scene, "fk_vorgesetzter")
        assert r.child_side is r.parent_side
        assert len(r.points) == 4
        assert r.points[0] != r.points[-1]

    def test_parallel_lines_do_not_share_endpoints(self, scene):
        a, b = rel(scene, "fk_lieferadresse"), rel(scene, "fk_rechnungsadresse")
        assert a.points[0] != b.points[0]
        assert a.points[-1] != b.points[-1]

    def test_parent_ends_on_same_side_are_spread(self, scene):
        ends: dict = {}
        for r in scene.relations:
            ends.setdefault((id(r.parent_item), r.parent_side), []).append(r.points[-1].y())
        spacing = 2 * scene.notation.end_width() + 4
        for ys in ends.values():
            ys = sorted(ys)
            assert all(b - a >= spacing - 1e-6 for a, b in zip(ys, ys[1:]))

    def test_moving_entity_updates_lines(self, scene):
        r = rel(scene, "fk_bestellung_kunde")
        before = list(r.points)
        r.parent_item.moveBy(0, 300)
        assert r.points != before
        p = r.parent_item.mapRectToScene(r.parent_item.rect)
        assert p.top() <= r.points[-1].y() <= p.bottom()

    def test_line_styles(self, scene):
        assert rel(scene, "fk_pos_bestellung").relationship.identifying
        assert not rel(scene, "fk_pos_artikel").relationship.identifying

    def test_highlight_follows_selection(self, scene):
        r = rel(scene, "fk_profil_kunde")
        assert not r.highlighted
        r.parent_item.setSelected(True)
        assert r.highlighted

    def test_tooltip(self, scene):
        tip = rel(scene, "fk_profil_kunde").toolTip()
        assert "fk_profil_kunde" in tip and "1:1" in tip and "shop.kunde" in tip

    def test_lines_behind_entities(self, scene):
        assert all(r.zValue() < 0 for r in scene.relations)

    def test_paint_does_not_fail(self, scene):
        image = QImage(200, 200, QImage.Format.Format_ARGB32)
        painter = QPainter(image)
        for r in scene.relations:
            r.paint(painter, None)
        painter.end()
