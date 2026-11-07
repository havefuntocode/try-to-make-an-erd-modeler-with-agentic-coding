"""Tests für das Auto-Layout (ohne Qt) und die Ausweichlogik der Linien."""

import pytest
from PySide6.QtCore import QPointF, QRectF

from erd_editor.analysis import derive_relationships
from erd_editor.diagram.routing import Side, path_hits, route_avoiding
from erd_editor.layout import LayoutNode, LayoutOptions, layered_layout
from erd_editor.model import TableRef


def rect(pos, node):
    return (pos[0], pos[1], pos[0] + node.width, pos[1] + node.height)


def overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


@pytest.fixture
def layout_input(test_schema):
    nodes = [
        LayoutNode(ref, 150 + 5 * len(table.columns), 60 + 18 * len(table.columns), ref.database)
        for ref, table in test_schema.tables()
    ]
    edges = [(r.child, r.parent) for r in derive_relationships(test_schema)]
    return nodes, edges


class TestLayeredLayout:
    def test_every_node_gets_a_position(self, layout_input):
        nodes, edges = layout_input
        pos = layered_layout(nodes, edges)
        assert set(pos) == {n.key for n in nodes}

    def test_no_overlaps(self, layout_input):
        nodes, edges = layout_input
        pos = layered_layout(nodes, edges)
        rects = [rect(pos[n.key], n) for n in nodes]
        for i, a in enumerate(rects):
            for b in rects[i + 1 :]:
                assert not overlaps(a, b)

    def test_parents_left_of_children(self, layout_input):
        nodes, edges = layout_input
        by_key = {n.key: n for n in nodes}
        pos = layered_layout(nodes, edges)
        for child, parent in edges:
            if child == parent or child.database != parent.database:
                continue
            assert pos[parent][0] + by_key[parent].width < pos[child][0], (child, parent)

    def test_databases_are_separate_blocks(self, layout_input):
        nodes, edges = layout_input
        pos = layered_layout(nodes, edges)
        shop_bottom = max(pos[n.key][1] + n.height for n in nodes if n.group == "shop")
        master_top = min(pos[n.key][1] for n in nodes if n.group == "stammdaten")
        assert master_top >= shop_bottom + LayoutOptions().group_gap - 1e-6

    def test_deterministic(self, layout_input):
        nodes, edges = layout_input
        assert layered_layout(nodes, edges) == layered_layout(nodes, edges)

    def test_starts_at_origin(self, layout_input):
        nodes, edges = layout_input
        pos = layered_layout(nodes, edges)
        assert min(x for x, _ in pos.values()) == pytest.approx(0)
        assert min(y for _, y in pos.values()) == pytest.approx(0)

    def test_cycle_does_not_fail(self):
        nodes = [LayoutNode(k, 100, 50) for k in "abc"]
        pos = layered_layout(nodes, [("a", "b"), ("b", "a"), ("c", "a")])
        assert set(pos) == {"a", "b", "c"}
        assert pos["a"][0] == pos["b"][0]  # Zyklus in derselben Spalte
        assert pos["c"][0] > pos["a"][0]

    def test_isolated_nodes_below_connected(self):
        nodes = [LayoutNode(k, 100, 50) for k in ["p", "c", "x", "y"]]
        pos = layered_layout(nodes, [("c", "p")])
        assert pos["x"][1] > pos["p"][1] + 50
        assert pos["y"][1] == pos["x"][1]

    def test_self_loops_and_unknown_nodes_ignored(self):
        nodes = [LayoutNode("a", 100, 50)]
        assert set(layered_layout(nodes, [("a", "a"), ("a", "zz")])) == {"a"}

    def test_child_aligned_with_single_parent(self):
        nodes = [LayoutNode("p", 100, 80), LayoutNode("c", 100, 80)]
        pos = layered_layout(nodes, [("c", "p")])
        assert pos["c"][1] == pytest.approx(pos["p"][1])

    def test_empty(self):
        assert layered_layout([], []) == {}


class TestRouteAvoiding:
    def test_free_path_stays_simple(self):
        pts = route_avoiding(QPointF(100, 50), Side.RIGHT, QPointF(400, 50), Side.LEFT, 30, 0, [])
        assert pts == [QPointF(100, 50), QPointF(400, 50)]

    def test_avoids_box_in_between(self):
        blocker = QRectF(200, 0, 100, 200)
        pts = route_avoiding(QPointF(100, 100), Side.RIGHT, QPointF(450, 100), Side.LEFT, 30, 0, [blocker])
        assert not path_hits(pts, [blocker])
        assert pts[0] == QPointF(100, 100) and pts[-1] == QPointF(450, 100)
        for a, b in zip(pts, pts[1:]):
            assert a.x() == pytest.approx(b.x()) or a.y() == pytest.approx(b.y())

    def test_moves_vertical_segment_out_of_box(self):
        # Mittelstück läge in der Box; Ausweichen neben das Kind genügt
        blocker = QRectF(230, 60, 60, 60)
        pts = route_avoiding(QPointF(100, 20), Side.RIGHT, QPointF(420, 200), Side.LEFT, 30, 0, [blocker])
        assert not path_hits(pts, [blocker])


def test_scene_lines_avoid_boxes(qapp, test_schema):
    """Im Testschema läuft nach dem Auto-Layout keine Linie durch eine fremde Box."""
    from erd_editor.diagram.scene import DiagramScene

    scene = DiagramScene()
    scene.load(test_schema)
    rects = {id(i): i.mapRectToScene(i.rect) for i in scene.entities.values()}
    for r in scene.relations:
        others = [v for k, v in rects.items() if k not in (id(r.child_item), id(r.parent_item))]
        assert not path_hits(r.points, others, clearance=0), r.relationship.name
