"""Tests für die Notationen (min,max) und IDEF1X sowie den Notationswechsel."""

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QImage, QPainter

from erd_editor.analysis import EXACTLY_ONE, ZERO_OR_MANY, ZERO_OR_ONE, Cardinality, RelationshipOverride
from erd_editor.diagram.scene import DiagramScene
from erd_editor.model import Layout, TableRef
from erd_editor.notation import NOTATIONS, CrowsFootNotation, Idef1xNotation, MinMaxNotation, notation_for
from erd_editor.notation.idef1x import child_marker
from erd_editor.notation.minmax import minmax_label
from erd_editor.persistence.document import DiagramDocument, document_from_dict, document_to_dict


def shop(table: str) -> TableRef:
    return TableRef("shop", None, table)


# ---------------------------------------------------------------- reine Logik


def test_registry():
    assert list(NOTATIONS) == ["crowsfoot", "minmax", "idef1x"]
    assert isinstance(notation_for("idef1x"), Idef1xNotation)
    assert isinstance(notation_for("gibt_es_nicht"), CrowsFootNotation)


@pytest.mark.parametrize(
    "card, text", [(ZERO_OR_MANY, "(0,n)"), (Cardinality(1, None), "(1,n)"), (EXACTLY_ONE, "(1,1)"), (ZERO_OR_ONE, "(0,1)")]
)
def test_minmax_label(card, text):
    assert minmax_label(card) == text


@pytest.mark.parametrize(
    "card, marker", [(ZERO_OR_MANY, ""), (Cardinality(1, None), "P"), (ZERO_OR_ONE, "Z"), (EXACTLY_ONE, "1")]
)
def test_idef1x_child_marker(card, marker):
    assert child_marker(card) == marker


@pytest.mark.parametrize("cls", [CrowsFootNotation, MinMaxNotation, Idef1xNotation])
def test_all_notations_draw(qapp, cls):
    n = cls()
    image = QImage(200, 120, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    for direction in (QPointF(1, 0), QPointF(-1, 0)):
        for at_child, card, opposite in ((True, ZERO_OR_MANY, ZERO_OR_ONE), (False, ZERO_OR_ONE, ZERO_OR_MANY)):
            n.draw_end(painter, QPointF(100, 60), direction, card, opposite, at_child)
    painter.end()
    assert n.end_length(ZERO_OR_MANY) > 0 and n.end_width() > 0
    assert any(image.pixelColor(x, y) != QColor("white") for x in range(200) for y in range(120))


def test_idef1x_mandatory_parent_draws_nothing(qapp):
    image = QImage(60, 60, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    Idef1xNotation().draw_end(painter, QPointF(30, 30), QPointF(1, 0), EXACTLY_ONE, ZERO_OR_MANY, at_child=False)
    painter.end()
    assert all(image.pixelColor(x, y) == QColor("white") for x in range(60) for y in range(60))


# ---------------------------------------------------------------- Szene


@pytest.fixture
def scene(qapp, test_schema):
    s = DiagramScene()
    s.load(test_schema)
    return s


class TestScene:
    def test_switch_notation(self, scene):
        scene.set_notation("minmax")
        assert scene.notation.key == "minmax"
        assert all(r.notation is scene.notation for r in scene.relations)
        assert scene.current_layout().notation == "minmax"

    def test_idef1x_corners(self, scene):
        scene.set_notation("idef1x")
        assert not scene.entities[shop("bestellposition")].square_corners  # abhängig
        assert not scene.entities[shop("adresse")].square_corners  # abhängig
        assert scene.entities[shop("kunde")].square_corners  # unabhängig
        scene.set_notation("crowsfoot")
        assert not any(i.square_corners for i in scene.entities.values())

    def test_idef1x_corners_follow_overrides(self, scene):
        scene.set_notation("idef1x")
        scene.set_overrides({(shop("bestellung"), "fk_bestellung_kunde"): RelationshipOverride(identifying=True)})
        assert not scene.entities[shop("bestellung")].square_corners

    def test_self_reference_does_not_make_dependent(self, scene):
        scene.set_notation("idef1x")
        assert scene.entities[shop("mitarbeiter")].square_corners

    def test_saved_and_loaded(self, scene, test_schema):
        scene.set_notation("idef1x")
        layout = document_from_dict(document_to_dict(DiagramDocument(test_schema, scene.current_layout()))).layout
        assert layout.notation == "idef1x"
        other = DiagramScene()
        other.load(test_schema, layout)
        assert other.notation.key == "idef1x"
        assert other.entities[shop("kunde")].square_corners

    def test_restore_layout_switches_notation(self, scene):
        before = scene.current_layout()
        scene.set_notation("minmax")
        scene.restore_layout(before)
        assert scene.notation.key == "crowsfoot"

    def test_lines_still_avoid_boxes(self, scene):
        from erd_editor.diagram.routing import path_hits

        for key in NOTATIONS:
            scene.set_notation(key)
            scene.auto_layout()
            rects = {id(i): i.mapRectToScene(i.rect) for i in scene.entities.values()}
            for r in scene.relations:
                others = [v for k, v in rects.items() if k not in (id(r.child_item), id(r.parent_item))]
                assert not path_hits(r.points, others, clearance=0), (key, r.relationship.name)


# ---------------------------------------------------------------- Hauptfenster


@pytest.fixture
def window(qapp, test_schema):
    from erd_editor.main import MainWindow

    w = MainWindow()
    w.load_diagram(test_schema)
    yield w
    w.setWindowModified(False)
    w.close()


class TestMainWindow:
    def test_menu_and_undo(self, window):
        window.notation_actions["idef1x"].trigger()
        assert window.scene.notation.key == "idef1x"
        assert window.undo_stack.undoText() == "Notation IDEF1X"
        window.undo_stack.undo()
        assert window.scene.notation.key == "crowsfoot"
        assert window.notation_actions["crowsfoot"].isChecked()

    def test_notation_per_view(self, window):
        window.set_notation("minmax")
        window.add_subdiagram("B", [shop("kunde"), shop("bestellung")])
        assert window.scene.notation.key == "minmax"  # übernimmt „Gesamt“
        window.set_notation("idef1x")
        window.activate_view(None)
        assert window.scene.notation.key == "minmax"
        window.activate_view("B")
        assert window.scene.notation.key == "idef1x"

    def test_new_diagram_keeps_session_notation(self, window, test_schema):
        window.set_notation("idef1x")
        window.load_diagram(test_schema)
        assert window.scene.notation.key == "idef1x"
