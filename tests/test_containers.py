"""Tests für die Datenbank-Behälter."""

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QImage, QPainter

from erd_editor.diagram.container_item import ContainerItem
from erd_editor.diagram.palette import PALETTE, colors_for
from erd_editor.diagram.scene import DiagramScene
from erd_editor.model import Database, Dialect


@pytest.fixture
def scene(qapp, test_schema):
    s = DiagramScene()
    s.load(test_schema)
    return s


def test_one_container_per_database(scene):
    assert set(scene.containers) == {"shop", "stammdaten"}
    assert len(scene.containers["shop"].members) == 9
    assert [m.ref.table for m in scene.containers["stammdaten"].members] == ["land"]


def test_container_encloses_members(scene):
    for container in scene.containers.values():
        for item in container.members:
            box = item.mapRectToScene(item.rect)
            assert container.rect.contains(box)
            assert box.top() >= container.header_rect.bottom()


def test_containers_do_not_overlap_after_layout(scene):
    a, b = scene.containers["shop"].rect, scene.containers["stammdaten"].rect
    assert not a.intersects(b)


def test_containers_behind_lines_and_entities(scene):
    z_lines = min(r.zValue() for r in scene.relations)
    assert all(c.zValue() < z_lines for c in scene.containers.values())


def test_distinct_colors_and_tinted_entities(scene):
    shop, master = scene.containers["shop"], scene.containers["stammdaten"]
    assert shop.colors != master.colors
    entity = master.members[0]
    assert entity.style.header.name().lower() == master.colors.entity_header.lower()


def test_palette_wraps():
    assert colors_for(len(PALETTE)) == colors_for(0)


def test_container_grows_when_entity_moves(scene):
    container = scene.containers["shop"]
    before = container.rect
    item = container.members[0]
    item.moveBy(before.width(), 200)  # über den rechten Rand hinaus
    assert container.rect.width() > before.width()
    assert container.rect.contains(item.mapRectToScene(item.rect))


def test_single_member_container_follows(scene):
    container = scene.containers["stammdaten"]
    before = container.rect
    container.members[0].moveBy(400, 300)
    assert container.rect.size() == before.size()
    assert container.rect.topLeft() == before.topLeft() + QPointF(400, 300)


def test_moving_container_moves_members_and_lines(scene):
    container = scene.containers["stammdaten"]
    land = container.members[0]
    line = next(r for r in scene.relations if r.relationship.name == "fk_kunde_land")
    pos_before, rect_before, end_before = land.pos(), container.rect, line.points[-1]
    container.move_by(50, 80)
    assert land.pos() == pos_before + QPointF(50, 80)
    assert container.rect.topLeft() == rect_before.topLeft() + QPointF(50, 80)
    assert line.points[-1] != end_before


def test_info_text_and_tooltip(scene):
    shop = scene.containers["shop"]
    assert "MySQL" in shop.info_text and "utf8mb4" in shop.info_text
    assert "shop" in shop.toolTip()


def test_empty_database(qapp):
    item = ContainerItem(Database("leer", Dialect.MYSQL), colors_for(0))
    item.update_geometry()
    assert item.rect.width() > 0 and item.rect.height() > 0


def test_paint_does_not_fail(scene):
    image = QImage(300, 300, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    for c in scene.containers.values():
        c.paint(painter, None)
    painter.end()
