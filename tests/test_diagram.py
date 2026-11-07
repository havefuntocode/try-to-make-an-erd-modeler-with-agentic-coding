"""Tests für die Darstellung: Symbole, Entitätsboxen, Szene."""

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QImage, QPainter

from erd_editor.diagram.entity_item import EntityItem
from erd_editor.diagram.icons import ICON_DIR, renderer
from erd_editor.diagram.markers import INDEXED_ICON, KeyIcon, column_marker, index_label
from erd_editor.diagram.scene import DiagramScene
from erd_editor.diagram.view import DiagramView
from erd_editor.main import parse_target
from erd_editor.model import Column, ConnectionProfile, Dialect, Index, IndexKind, Table, TableRef
from erd_editor.persistence.credentials import account


def shop(table: str) -> TableRef:
    return TableRef("shop", None, table)


# ---------------------------------------------------------------- Symbole (ohne Qt)


class TestMarkers:
    def marker(self, schema, table, column):
        t = schema.find_table(shop(table))
        return column_marker(t, t.column(column))

    def test_pk(self, test_schema):
        assert self.marker(test_schema, "kunde", "id").icon is KeyIcon.PRIMARY

    def test_fk(self, test_schema):
        assert self.marker(test_schema, "bestellung", "kunde_id").icon is KeyIcon.FOREIGN

    def test_pk_and_fk(self, test_schema):
        assert self.marker(test_schema, "bestellposition", "bestellung_id").icon is KeyIcon.PRIMARY_FOREIGN

    def test_plain_columns(self, test_schema):
        assert self.marker(test_schema, "bestellposition", "menge").icon is KeyIcon.NOT_NULL
        assert self.marker(test_schema, "kundenprofil", "bio").icon is KeyIcon.NULLABLE

    def test_indexed_flag(self, test_schema):
        assert self.marker(test_schema, "kunde", "name").indexed  # FULLTEXT
        assert self.marker(test_schema, "bestellung", "kunde_id").indexed
        assert not self.marker(test_schema, "kunde", "id").indexed  # PK: kein Zusatzsymbol
        assert not self.marker(test_schema, "kunde", "land_code").indexed

    def test_index_label(self):
        assert index_label(Index("uq_ab", IndexKind.UNIQUE, ("a", "b"))) == ("UNIQUE", "uq_ab (a, b)")
        assert index_label(Index("PRIMARY", IndexKind.PRIMARY, ("id",))) == ("PRIMARY", "(id)")


def test_all_icons_are_valid_svg(qapp):
    for name in [*(k.value for k in KeyIcon), INDEXED_ICON]:
        assert (ICON_DIR / f"{name}.svg").exists()
        assert renderer(name).isValid()


# ---------------------------------------------------------------- Hilfsfunktionen


def test_parse_target():
    assert parse_target("root@localhost") == ("root", "localhost", None)
    assert parse_target("app@db.local:3307") == ("app", "db.local", 3307)
    with pytest.raises(ValueError):
        parse_target("localhost")


def test_credentials_account():
    profile = ConnectionProfile("x", Dialect.MYSQL, "localhost", 3306, "root")
    assert account(profile) == "mysql://root@localhost:3306"
    assert account(ConnectionProfile("y", Dialect.MYSQL, "h", None, "u")) == "mysql://u@h"


# ---------------------------------------------------------------- Entitätsbox


@pytest.fixture
def kunde_item(qapp, test_schema):
    return EntityItem(shop("kunde"), test_schema.find_table(shop("kunde")))


class TestEntityItem:
    def test_size_grows_with_content(self, qapp):
        small = EntityItem(shop("a"), Table("a", [Column("id", "int")]))
        big = EntityItem(
            shop("b"),
            Table("b", [Column(f"spalte_mit_langem_namen_{i}", "varchar(255)") for i in range(10)]),
        )
        assert big.rect.width() > small.rect.width()
        assert big.rect.height() > small.rect.height()

    def test_min_width(self, qapp):
        item = EntityItem(shop("a"), Table("a"))
        assert item.rect.width() >= item.style.min_width

    def test_bounding_rect_contains_outline(self, kunde_item):
        assert kunde_item.boundingRect().contains(kunde_item.rect)

    def test_column_anchors_are_ordered(self, kunde_item):
        ys = [kunde_item.column_anchor_y(c) for c in ("id", "name", "land_code")]
        assert all(y is not None for y in ys)
        assert ys == sorted(ys)
        assert kunde_item.column_anchor_y("gibts_nicht") is None

    def test_index_area_below_columns(self, kunde_item):
        last_column = kunde_item.column_anchor_y("land_code")
        assert kunde_item._index_top > last_column
        assert len(kunde_item._indexes) == 2

    def test_tooltips(self, kunde_item):
        header = kunde_item.tooltip_at(QPointF(10, 2))
        assert "shop.kunde" in header and "Kundenstamm" in header
        row = kunde_item.tooltip_at(QPointF(10, kunde_item.column_anchor_y("land_code")))
        assert "land_code" in row and "char(2)" in row and "NULL" in row

    def test_tooltip_escapes_html(self, qapp):
        item = EntityItem(shop("t"), Table("t", comment="a < b & c"))
        assert "a &lt; b &amp; c" in item.tooltip_at(QPointF(1, 1))

    def test_paint_does_not_fail(self, kunde_item):
        image = QImage(400, 300, QImage.Format.Format_ARGB32)
        painter = QPainter(image)
        kunde_item.paint(painter, None)
        kunde_item.setSelected(True)
        kunde_item.paint(painter, None)
        painter.end()


# ---------------------------------------------------------------- Szene und Ansicht


class TestScene:
    def test_one_item_per_table(self, qapp, test_schema):
        scene = DiagramScene()
        scene.load(test_schema)
        assert len(scene.entities) == 10
        assert set(scene.entities) == {ref for ref, _ in test_schema.tables()}

    def test_items_do_not_overlap(self, qapp, test_schema):
        scene = DiagramScene()
        scene.load(test_schema)
        rects = [item.sceneBoundingRect() for item in scene.entities.values()]
        for i, a in enumerate(rects):
            for b in rects[i + 1 :]:
                assert not a.intersects(b)

    def test_reload_replaces_items(self, qapp, test_schema):
        scene = DiagramScene()
        scene.load(test_schema)
        scene.load(test_schema)
        assert len(scene.items()) == 10 + 11  # Boxen + Linien
        assert len(scene.relations) == 11


def test_view_zoom_limits(qapp):
    view = DiagramView(DiagramScene())
    view.set_zoom(100)
    assert view.zoom == pytest.approx(DiagramView.MAX_ZOOM)
    view.set_zoom(0.001)
    assert view.zoom == pytest.approx(DiagramView.MIN_ZOOM)
    view.set_zoom(1.0)
    view.zoom_in()
    assert view.zoom == pytest.approx(DiagramView.ZOOM_STEP)


def test_main_window_loads_diagram(qapp, test_schema):
    from erd_editor.main import MainWindow

    window = MainWindow()
    window.load_diagram(test_schema)
    assert len(window.scene.entities) == 10
    assert "10 Tabellen" in window.statusBar().currentMessage()


def test_primary_key_listed_first(qapp):
    table = Table(
        "t",
        [Column("id", "int"), Column("a", "int")],
        [Index("ix_a", IndexKind.INDEX, ("a",)), Index("PRIMARY", IndexKind.PRIMARY, ("id",))],
    )
    item = EntityItem(shop("t"), table)
    assert [r.kind for r in item._indexes] == ["PRIMARY", "INDEX"]
