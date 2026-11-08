"""Tests für manuelle Überschreibungen (Mindestkardinalität, identifizierend)."""

import pytest
from PySide6.QtCore import QPointF

from erd_editor.analysis import (
    RelationshipOverride,
    apply_overrides,
    derive_relationships,
    describe_override,
    relationship_key,
)
from erd_editor.diagram.scene import DiagramScene
from erd_editor.model import TableRef
from erd_editor.persistence.document import DiagramDocument, document_from_dict, document_to_dict


def shop(table: str) -> TableRef:
    return TableRef("shop", None, table)


POS_KEY = (shop("bestellposition"), "fk_pos_bestellung")
KUNDE_KEY = (shop("bestellung"), "fk_bestellung_kunde")


def rels(diagram, overrides=None):
    return {r.name: r for r in apply_overrides(derive_relationships(diagram), overrides or {})}


class TestApply:
    def test_without_override_unchanged(self, test_schema):
        r = rels(test_schema)["fk_pos_bestellung"]
        assert str(r.child_end) == "0..*" and not r.manual

    def test_min_one_child(self, test_schema):
        r = rels(test_schema, {POS_KEY: RelationshipOverride(child_min_one=True)})["fk_pos_bestellung"]
        assert str(r.child_end) == "1..*" and r.manual
        assert r.identifying  # unverändert

    def test_identifying_override(self, test_schema):
        r = rels(test_schema, {KUNDE_KEY: RelationshipOverride(identifying=True)})["fk_bestellung_kunde"]
        assert r.identifying and r.manual
        r = rels(test_schema, {POS_KEY: RelationshipOverride(identifying=False)})["fk_pos_bestellung"]
        assert not r.identifying

    def test_empty_override_is_ignored(self, test_schema):
        assert not rels(test_schema, {POS_KEY: RelationshipOverride()})["fk_pos_bestellung"].manual

    def test_key(self, test_schema):
        assert relationship_key(rels(test_schema)["fk_pos_bestellung"]) == POS_KEY

    def test_describe(self):
        assert describe_override(RelationshipOverride(True, False)) == "mindestens ein Kind, nicht identifizierend"
        assert describe_override(RelationshipOverride()) == ""


def test_file_roundtrip(test_schema):
    doc = DiagramDocument(test_schema, overrides={
        POS_KEY: RelationshipOverride(child_min_one=True),
        KUNDE_KEY: RelationshipOverride(identifying=False),
        (shop("kunde"), "fk_kunde_land"): RelationshipOverride(),  # leer → nicht gespeichert
    })
    data = document_to_dict(doc)
    assert len(data["overrides"]) == 2
    assert document_from_dict(data).overrides == {
        POS_KEY: RelationshipOverride(child_min_one=True),
        KUNDE_KEY: RelationshipOverride(identifying=False),
    }


def test_scene_rebuilds_lines(qapp, test_schema):
    scene = DiagramScene()
    scene.load(test_schema)
    count = len(scene.relations)
    scene.set_overrides({KUNDE_KEY: RelationshipOverride(identifying=True)})
    line = next(r for r in scene.relations if r.relationship.name == "fk_bestellung_kunde")
    assert line.relationship.identifying and "(manuell)" in line.toolTip()
    assert len(scene.relations) == count and len([i for i in scene.items() if hasattr(i, "relationship")]) == count


@pytest.fixture
def window(qapp, test_schema):
    from erd_editor.main import MainWindow

    w = MainWindow()
    w.load_diagram(test_schema)
    yield w
    w.setWindowModified(False)
    w.close()


def line(w, name):
    return next(r for r in w.scene.relations if r.relationship.name == name)


class TestMainWindow:
    def test_set_override_and_undo(self, window):
        window.set_override(POS_KEY, RelationshipOverride(child_min_one=True))
        assert str(line(window, "fk_pos_bestellung").relationship.child_end) == "1..*"
        assert window.undo_stack.undoText() == "„fk_pos_bestellung“: mindestens ein Kind"
        window.undo_stack.undo()
        assert str(line(window, "fk_pos_bestellung").relationship.child_end) == "0..*"
        assert window.document.overrides == {}

    def test_reset_removes_entry(self, window):
        window.set_override(POS_KEY, RelationshipOverride(identifying=False))
        window.set_override(POS_KEY, RelationshipOverride())
        assert POS_KEY not in window.document.overrides
        assert line(window, "fk_pos_bestellung").relationship.identifying

    def test_overrides_apply_in_subdiagrams(self, window):
        window.set_override(KUNDE_KEY, RelationshipOverride(identifying=True))
        window.add_subdiagram("B", [shop("kunde"), shop("bestellung")])
        assert line(window, "fk_bestellung_kunde").relationship.identifying

    def test_derived_identifying_ignores_override(self, window):
        window.set_override(KUNDE_KEY, RelationshipOverride(identifying=True))
        assert window._derived_identifying(line(window, "fk_bestellung_kunde").relationship) is False

    def test_right_click_on_line_emits_signal(self, window):
        """Rechtsklick auf eine Linie meldet genau diese Beziehung."""
        from PySide6.QtCore import QEvent, QPoint
        from PySide6.QtWidgets import QGraphicsSceneContextMenuEvent

        target = line(window, "fk_bestellung_kunde")
        a, b = target.points[0], target.points[1]
        event = QGraphicsSceneContextMenuEvent(QEvent.Type.GraphicsSceneContextMenu)
        event.setScenePos(QPointF((a.x() + b.x()) / 2, (a.y() + b.y()) / 2))
        event.setScreenPos(QPoint(10, 10))
        got = []
        window.scene.relation_context_requested.disconnect()
        window.scene.relation_context_requested.connect(lambda item, pos: got.append(item.relationship.name))
        window.scene.contextMenuEvent(event)
        assert got == ["fk_bestellung_kunde"]

    def test_menu_contents_and_actions(self, window):
        window.set_override(POS_KEY, RelationshipOverride(child_min_one=True))
        menu = window.build_relation_menu(line(window, "fk_pos_bestellung"))
        actions = {a.text(): a for a in menu.actions()}
        assert menu.actions()[0].text() == "Beziehung fk_pos_bestellung"
        assert actions["Elternteil hat mindestens ein Kind"].isChecked()
        assert actions["Auf automatisch zurücksetzen"].isEnabled()
        ident = {a.text(): a for a in actions["Identifizierend"].menu().actions()}
        assert ident["Automatisch (abgeleitet: ja)"].isChecked()
        ident["Nein"].trigger()
        assert not line(window, "fk_pos_bestellung").relationship.identifying
        assert window.document.overrides[POS_KEY] == RelationshipOverride(child_min_one=True, identifying=False)
