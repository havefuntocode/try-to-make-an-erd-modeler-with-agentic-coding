"""Tests für die Darstellung: Symbole, Entitätsboxen, Szene."""

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage, QPainter

from erd_editor.diagram.entity_item import EntityItem
from erd_editor.diagram.icons import ICON_DIR, renderer
from erd_editor.diagram.markers import INDEXED_ICON, ExtraIcon, KeyIcon, column_index_notes, column_marker, index_label
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

    def test_unique_markers(self, test_schema):
        assert self.marker(test_schema, "kundenprofil", "kunde_id").extra is ExtraIcon.UNIQUE  # uq_kunde
        assert self.marker(test_schema, "bestellung", "kunde_id").extra is ExtraIcon.INDEXED

    def test_composite_unique_marks_each_part(self):
        from erd_editor.model import Column, Table

        t = Table("termin", [Column("id", "int", False), Column("kunde_id", "int", False),
                             Column("datum", "date", False), Column("code", "int", False)],
                  [Index("PRIMARY", IndexKind.PRIMARY, ("id",)),
                   Index("uq_termin", IndexKind.UNIQUE, ("kunde_id", "datum")),
                   Index("uq_code", IndexKind.UNIQUE, ("code",)),
                   Index("ix_code_datum", IndexKind.INDEX, ("code", "datum"))])
        extra = {c.name: column_marker(t, c).extra for c in t.columns}
        assert extra == {"id": None, "kunde_id": ExtraIcon.UNIQUE_PART,
                         "datum": ExtraIcon.UNIQUE_PART, "code": ExtraIcon.UNIQUE}  # allein eindeutig gewinnt
        assert column_index_notes(t, t.column("datum")) == [
            "Teil von UNIQUE uq_termin (kunde_id, datum)", "Teil von INDEX ix_code_datum (code, datum)"]
        assert column_index_notes(t, t.column("code"))[0] == "UNIQUE uq_code (code)"
        assert column_index_notes(t, t.column("id")) == []  # PK steht schon im Symbol

    def test_unique_on_composite_pk_column(self):
        from erd_editor.model import Column, Table

        t = Table("pos", [Column("a", "int", False), Column("b", "int", False)],
                  [Index("PRIMARY", IndexKind.PRIMARY, ("a", "b")), Index("uq_b", IndexKind.UNIQUE, ("b",))])
        assert column_marker(t, t.column("a")).extra is None
        assert column_marker(t, t.column("b")).extra is ExtraIcon.UNIQUE

    def test_tooltip_lists_indexes(self, test_schema):
        from erd_editor.diagram.entity_item import column_tooltip

        t = test_schema.find_table(shop("kundenprofil"))
        assert "<br>UNIQUE uq_kunde (kunde_id)" in column_tooltip(t.column("kunde_id"), t)
        assert "UNIQUE" not in column_tooltip(t.column("kunde_id"))

    def test_index_label(self):
        assert index_label(Index("uq_ab", IndexKind.UNIQUE, ("a", "b"))) == ("UNIQUE", "uq_ab (a, b)")
        assert index_label(Index("PRIMARY", IndexKind.PRIMARY, ("id",))) == ("PRIMARY", "(id)")


def test_all_icons_are_valid_svg(qapp):
    from erd_editor.diagram.markers import CheckIcon

    for name in [*(k.value for k in KeyIcon), *(e.value for e in ExtraIcon), *(c.value for c in CheckIcon), INDEXED_ICON]:
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
        assert len(scene.items()) == 10 + 11 + 2  # Boxen + Linien + Behälter
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


class TestFitToWindow:
    """Ganzes Diagramm folgt der Fenstergröße – bis man selbst zoomt."""

    @pytest.fixture
    def view(self, qapp, test_schema):
        scene = DiagramScene()
        scene.load(test_schema)
        v = DiagramView(scene)
        v.resize(600, 400)
        v.show()
        qapp.processEvents()
        v.zoom_to_fit()
        yield v
        v.close()

    def resized(self, qapp, view, w, h):
        view.resize(w, h)
        qapp.processEvents()
        return view.zoom

    def test_bigger_window_enlarges_diagram(self, qapp, view):
        small = view.zoom
        assert view.fit_mode and small < 1.0
        assert self.resized(qapp, view, 1200, 800) > small * 1.5  # z. B. Vollbild
        assert self.resized(qapp, view, 600, 400) == pytest.approx(small)

    def test_manual_zoom_stops_following(self, qapp, view):
        for manual in (view.zoom_in, view.zoom_out, lambda: view.set_zoom(0.5)):
            view.zoom_to_fit()
            manual()
            assert not view.fit_mode
            before = view.zoom
            assert self.resized(qapp, view, 1300, 900) == pytest.approx(before)
            self.resized(qapp, view, 600, 400)

    def test_wheel_stops_following(self, qapp, view):
        from PySide6.QtCore import QPoint, QPointF
        from PySide6.QtGui import QWheelEvent

        event = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(), QPoint(0, 120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        view.wheelEvent(event)
        assert not view.fit_mode

    def test_ctrl_0_resumes_following(self, qapp, view):
        view.set_zoom(0.3)
        view.zoom_to_fit()
        assert view.fit_mode
        assert self.resized(qapp, view, 1200, 800) > 0.3

    def test_small_diagram_stays_at_100_percent(self, qapp):
        from erd_editor.model import Column, Database, Diagram, Dialect, Schema, Table

        scene = DiagramScene()
        scene.load(Diagram([Database("x", Dialect.MYSQL, [Schema(None, [Table("t", [Column("id", "int")])])])]))
        v = DiagramView(scene)
        v.resize(800, 600)
        v.show()
        v.zoom_to_fit()
        assert self.resized(qapp, v, 1600, 1000) == pytest.approx(1.0)
        assert v.fit_mode  # bleibt im Einpass-Modus, auch wenn 100 % die Grenze ist
        v.close()
