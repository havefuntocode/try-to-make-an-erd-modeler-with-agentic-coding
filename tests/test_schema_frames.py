"""Tests für Schemas als innere Rahmen (PostgreSQL, SQL Server)."""

import pytest
from PySide6.QtCore import QPointF, QRectF

from erd_editor.diagram.routing import Side, path_hits, route_avoiding
from erd_editor.diagram.scene import DiagramScene
from erd_editor.diagram.schema_item import SchemaItem
from erd_editor.model import Column, Layout, Schema, Table, TableRef
from erd_editor.persistence.document import DiagramDocument, document_from_dict, document_to_dict


def ref(schema: str, table: str) -> TableRef:
    return TableRef("erd", schema, table)


@pytest.fixture
def scene(qapp, schema_diagram):
    s = DiagramScene()
    s.load(schema_diagram)
    return s


def rect(item):
    return item.mapRectToScene(item.rect)


def test_one_frame_per_schema(scene):
    assert set(scene.schema_items) == {("erd", "shop"), ("erd", "stammdaten")}
    assert len(scene.schema_items[("erd", "shop")].members) == 9
    assert [m.ref.table for m in scene.schema_items[("erd", "stammdaten")].members] == ["land"]
    assert all(isinstance(s, SchemaItem) for s in scene.containers["erd"].schemas)


def test_mysql_has_no_schema_frames(qapp, test_schema):
    s = DiagramScene()
    s.load(test_schema)
    assert s.schema_items == {}


def test_nesting(scene):
    db = scene.containers["erd"]
    for schema in scene.schema_items.values():
        assert db.rect.contains(schema.rect)
        assert schema.rect.top() >= db.header_rect.bottom()
        for item in schema.members:
            assert schema.rect.contains(rect(item))
            assert rect(item).top() >= schema.header_rect.bottom()


def test_schema_frames_do_not_overlap(scene):
    a, b = (s.rect for s in scene.schema_items.values())
    assert not a.intersects(b)


def test_z_order(scene):
    schema = next(iter(scene.schema_items.values()))
    assert scene.containers["erd"].zValue() < schema.zValue() < min(r.zValue() for r in scene.relations)


def test_texts(scene):
    shop = scene.schema_items[("erd", "shop")]
    assert shop.title == "shop"
    assert shop.info_text == "Schema · 9 Tabellen"
    assert "erd.shop" in shop.toolTip()


def test_moving_entity_grows_both_frames(scene):
    schema, db = scene.schema_items[("erd", "stammdaten")], scene.containers["erd"]
    land = schema.members[0]
    land.moveBy(2000, 0)
    assert schema.rect.contains(rect(land))
    assert db.rect.contains(schema.rect)


def test_moving_schema_moves_members_and_grows_db(scene):
    schema, db = scene.schema_items[("erd", "stammdaten")], scene.containers["erd"]
    land = schema.members[0]
    before = land.pos()
    schema.move_by(0, 500)
    assert land.pos() == before + QPointF(0, 500)
    assert db.rect.contains(schema.rect)


def test_moving_database_moves_schema_frames(scene):
    schema, db = scene.schema_items[("erd", "shop")], scene.containers["erd"]
    schema.resize_to(schema.rect.adjusted(0, 0, 300, 0))
    before = schema.rect
    db.move_by(40, 60)
    assert schema.rect.topLeft() == before.topLeft() + QPointF(40, 60)
    assert schema.rect.size() == before.size()


def test_schema_frame_saved_and_restored(scene, schema_diagram):
    schema = scene.schema_items[("erd", "stammdaten")]
    schema.resize_to(schema.rect.adjusted(0, 0, 250, 80))
    expected = schema.rect
    layout = document_from_dict(document_to_dict(DiagramDocument(schema_diagram, scene.current_layout()))).layout
    assert ("erd", "stammdaten") in layout.schema_frames
    other = DiagramScene()
    other.load(schema_diagram, layout)
    got = other.schema_items[("erd", "stammdaten")].rect
    assert got.width() == pytest.approx(expected.width(), abs=0.2)
    assert got.height() == pytest.approx(expected.height(), abs=0.2)


def test_reset_and_auto_layout_clear_schema_frames(scene):
    schema = scene.schema_items[("erd", "shop")]
    schema.resize_to(schema.rect.adjusted(0, 0, 300, 0))
    scene.fit_frames_to_content()
    assert schema.manual_rect is None
    schema.resize_to(schema.rect.adjusted(0, 0, 300, 0))
    scene.auto_layout()
    assert schema.manual_rect is None


def test_scaling_keeps_nesting(scene):
    scene.set_display_scale(1.75)
    test_nesting(scene)


def test_new_table_lands_in_its_schema(scene, schema_diagram):
    layout = scene.current_layout()
    schema_diagram.database("erd").schema("stammdaten").tables.append(Table("waehrung", [Column("code", "char(3)")]))
    scene.load(schema_diagram, layout)
    new = rect(scene.entities[ref("stammdaten", "waehrung")])
    stammdaten = scene.schema_items[("erd", "stammdaten")]
    assert stammdaten.rect.contains(new)
    assert not scene.schema_items[("erd", "shop")].rect.intersects(stammdaten.rect)


def test_new_schema_gets_its_own_frame(scene, schema_diagram):
    layout = scene.current_layout()
    schema_diagram.database("erd").schemas.append(Schema("lager", [Table("regal", [Column("id", "integer")])]))
    scene.load(schema_diagram, layout)
    lager = scene.schema_items[("erd", "lager")]
    for other in ("shop", "stammdaten"):
        assert not lager.rect.intersects(scene.schema_items[("erd", other)].rect)
    assert scene.containers["erd"].rect.contains(lager.rect)


def test_lines_avoid_boxes(scene):
    rects = {id(i): rect(i) for i in scene.entities.values()}
    for r in scene.relations:
        others = [v for k, v in rects.items() if k not in (id(r.child_item), id(r.parent_item))]
        assert not path_hits(r.points, others, clearance=0), r.relationship.name


def test_u_route_goes_around_blocking_box():
    blocker = QRectF(90, 100, 200, 80)  # liegt im Weg der U-Linie rechts
    pts = route_avoiding(QPointF(100, 50), Side.RIGHT, QPointF(80, 300), Side.RIGHT, 30, 0, [blocker])
    assert not path_hits(pts, [blocker])
    assert pts[1].x() > blocker.right()


def test_old_layout_without_schema_frames():
    layout = document_from_dict({"format": "erd-editor", "version": 1, "layout": {}}).layout
    assert layout == Layout()
