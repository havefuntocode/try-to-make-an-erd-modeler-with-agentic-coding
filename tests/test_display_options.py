"""Tests für die Darstellungsoptionen und die kompaktere Anordnung großer Schemas."""

import pytest
from PySide6.QtCore import QPointF

from erd_editor.diagram.entity_item import EntityItem, hidden_label, visible_columns
from erd_editor.diagram.scene import DiagramScene
from erd_editor.layout import LayoutNode, layered_layout
from erd_editor.model import ColumnMode, DisplayOptions, Layout, TableRef
from erd_editor.persistence.document import DiagramDocument, document_from_dict, document_to_dict

KEYS = DisplayOptions(columns=ColumnMode.KEYS)
NONE = DisplayOptions(columns=ColumnMode.NONE)


def shop(table: str) -> TableRef:
    return TableRef("shop", None, table)


def item(test_schema, table, options=None):
    return EntityItem(shop(table), test_schema.find_table(shop(table)), options=options)


# ---------------------------------------------------------------- reine Logik


def test_visible_columns(test_schema):
    pos = test_schema.find_table(shop("bestellposition"))
    assert [c.name for c in visible_columns(pos, ColumnMode.ALL)] == ["bestellung_id", "pos", "artikel_id", "menge"]
    assert [c.name for c in visible_columns(pos, ColumnMode.KEYS)] == ["bestellung_id", "pos", "artikel_id"]
    assert visible_columns(pos, ColumnMode.NONE) == []


def test_hidden_label():
    assert hidden_label(1, ColumnMode.KEYS) == "+ 1 weitere Spalte"
    assert hidden_label(3, ColumnMode.KEYS) == "+ 3 weitere Spalten"
    assert hidden_label(5, ColumnMode.NONE) == "5 Spalten"


# ---------------------------------------------------------------- Entitätsbox


class TestEntityOptions:
    def test_keys_only_is_smaller_and_shows_hint(self, qapp, test_schema):
        full, keys = item(test_schema, "kunde"), item(test_schema, "kunde", KEYS)
        # eine ausgeblendete Spalte wird durch die Hinweiszeile ersetzt: gleich hoch, aber schmaler
        assert keys.rect.height() <= full.rect.height()
        assert keys.rect.width() < full.rect.width()
        assert keys._more_text == "+ 1 weitere Spalte"  # name ist weder PK noch FK
        assert keys.column_anchor_y("land_code") is not None  # FK-Spalte bleibt sichtbar
        assert keys.column_anchor_y("name") is None

    def test_none_shows_only_count(self, qapp, test_schema):
        box = item(test_schema, "kunde", DisplayOptions(columns=ColumnMode.NONE, show_indexes=False))
        assert box._columns == [] and box._more_text == "3 Spalten"
        assert box.column_anchor_y("id") is None

    def test_types_off_is_narrower(self, qapp, test_schema):
        with_types = item(test_schema, "bestellung")
        without = item(test_schema, "bestellung", DisplayOptions(show_types=False, show_indexes=False))
        assert without.rect.width() < with_types.rect.width()

    def test_indexes_off(self, qapp, test_schema):
        box = item(test_schema, "kunde", DisplayOptions(show_indexes=False))
        assert box._index_top is None and box._indexes == []

    def test_comment_shown_and_elided(self, qapp, test_schema):
        test_schema.find_table(shop("kunde")).comment = "Sehr langer Kommentar " * 20
        box = item(test_schema, "kunde", DisplayOptions(show_comments=True))
        assert box._comment_top is not None
        assert box._comment_text.endswith("…")
        assert box.rect.width() < 400
        assert item(test_schema, "kunde")._comment_top is None  # Standard: aus

    def test_set_options_recomputes(self, qapp, test_schema):
        box = item(test_schema, "kunde")
        height = box.rect.height()
        box.set_options(NONE)
        assert box.rect.height() < height
        box.set_options(DisplayOptions())
        assert box.rect.height() == height

    def test_hidden_columns_tooltip(self, qapp, test_schema):
        box = item(test_schema, "kunde", KEYS)
        tip = box.tooltip_at(QPointF(10, box._more_top + 2))
        assert "Ausgeblendet" in tip and "name" in tip

    def test_paint_all_variants(self, qapp, test_schema):
        from PySide6.QtGui import QImage, QPainter

        test_schema.find_table(shop("kunde")).comment = "Kundenstamm"
        image = QImage(400, 300, QImage.Format.Format_ARGB32)
        painter = QPainter(image)
        for mode in ColumnMode:
            for flag in (True, False):
                opts = DisplayOptions(mode, show_types=flag, show_indexes=flag, show_comments=flag)
                item(test_schema, "kunde", opts).paint(painter, None)
        painter.end()


# ---------------------------------------------------------------- Szene


@pytest.fixture
def scene(qapp, test_schema):
    s = DiagramScene()
    s.load(test_schema)
    return s


class TestSceneOptions:
    def test_applies_to_all_boxes_and_keeps_positions(self, scene):
        before = {r: i.pos() for r, i in scene.entities.items()}
        scene.set_display_options(KEYS)
        assert all(i.options == KEYS for i in scene.entities.values())
        assert {r: i.pos() for r, i in scene.entities.items()} == before

    def test_frames_follow_smaller_boxes(self, scene):
        height = scene.containers["shop"].rect.height()
        scene.set_display_options(NONE)
        assert scene.containers["shop"].rect.height() < height

    def test_lines_dock_in_middle_when_columns_hidden(self, scene):
        scene.set_display_options(NONE)
        line = next(r for r in scene.relations if r.relationship.name == "fk_bestellung_kunde")
        child = line.child_item.mapRectToScene(line.child_item.rect)
        assert line.points[0].y() == pytest.approx(child.center().y())

    def test_overlap_detection(self, scene):
        assert not scene.has_overlaps()
        scene.set_display_options(NONE)
        assert not scene.set_display_options(DisplayOptions())  # zurück auf die Ausgangsgröße
        a, b = scene.entities[shop("kunde")], scene.entities[shop("artikel")]
        b.setPos(a.pos() + QPointF(5, 5))
        assert scene.has_overlaps()

    def test_saved_and_restored(self, scene, test_schema):
        opts = DisplayOptions(ColumnMode.KEYS, show_types=False, show_indexes=False, show_comments=True)
        scene.set_display_options(opts)
        layout = document_from_dict(document_to_dict(DiagramDocument(test_schema, scene.current_layout()))).layout
        assert layout.display == opts
        other = DiagramScene()
        other.load(test_schema, layout)
        assert other.entities[shop("kunde")].options == opts

    def test_old_files_use_defaults(self):
        assert document_from_dict({"format": "erd-editor", "version": 1}).layout.display == DisplayOptions()

    def test_emits_layout_changed_once_changed(self, scene):
        calls = []
        scene.layout_changed.connect(lambda: calls.append(1))
        scene.set_display_options(DisplayOptions())  # unverändert
        assert calls == []
        scene.set_display_options(KEYS)
        assert calls


def test_main_window_menu(qapp, test_schema):
    from erd_editor.main import MainWindow

    w = MainWindow()
    w.load_diagram(test_schema)
    w.column_actions[ColumnMode.KEYS].trigger()
    assert w.scene.display_options.columns is ColumnMode.KEYS
    w.option_actions["show_types"].setChecked(False)
    assert not w.scene.display_options.show_types
    assert w.isWindowModified()
    # Laden eines Dokuments setzt das Menü passend
    from erd_editor.persistence.document import DiagramDocument

    w.show_document(DiagramDocument(test_schema, Layout(display=DisplayOptions(show_comments=True))), use_layout=True)
    assert w.option_actions["show_comments"].isChecked()
    assert w.column_actions[ColumnMode.ALL].isChecked()
    w.setWindowModified(False)
    w.close()


# ---------------------------------------------------------------- kompaktere Anordnung


class TestCompactLayout:
    def test_many_small_groups_fill_rows(self):
        """20 Paare: nicht untereinander, sondern in Zeilen (Querformat)."""
        nodes, edges = [], []
        for i in range(20):
            nodes += [LayoutNode(f"p{i}", 150, 80), LayoutNode(f"c{i}", 150, 80)]
            edges.append((f"c{i}", f"p{i}"))
        pos = layered_layout(nodes, edges)
        width = max(x + 150 for x, _ in pos.values())
        height = max(y + 80 for _, y in pos.values())
        assert width > height  # Querformat statt Turm

    def test_isolated_grid_is_wide(self):
        nodes = [LayoutNode(f"t{i}", 150, 80) for i in range(50)]
        pos = layered_layout(nodes, [])
        columns = len({x for x, _ in pos.values()})
        assert columns >= 8

    def test_no_overlaps_with_mixed_sizes(self):
        nodes = [LayoutNode(f"t{i}", 100 + (i % 5) * 40, 60 + (i % 7) * 25) for i in range(40)]
        edges = [(f"t{i}", f"t{i - 1}") for i in range(1, 40, 3)]
        pos = layered_layout(nodes, edges)
        by_key = {n.key: n for n in nodes}
        rects = [(x, y, x + by_key[k].width, y + by_key[k].height) for k, (x, y) in pos.items()]
        for i, a in enumerate(rects):
            for b in rects[i + 1 :]:
                assert not (a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3])
