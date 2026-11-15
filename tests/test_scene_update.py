"""Szene gezielt aktualisieren statt komplett neu aufbauen (Version 3)."""

import copy
from dataclasses import replace

import pytest
from PySide6.QtWidgets import QApplication

from erd_editor.diagram.scene import DiagramScene
from erd_editor.model import Column, Dialect, Index, IndexKind, Table, TableRef
from erd_editor.model.editing import (
    add_database,
    add_relationship,
    add_table,
    delete_relationship,
    delete_table,
    replace_table,
)


def shop(table: str) -> TableRef:
    return TableRef("shop", None, table)


@pytest.fixture
def test_schema(test_schema):
    """Testschema als Modell (bearbeitbar)."""
    for db in test_schema.databases:
        db.modeled = True
    return test_schema


@pytest.fixture
def scene(qapp, test_schema):
    s = DiagramScene()
    s.load(test_schema)
    return s


def edited(diagram, ref, change):
    """Tiefe Kopie mit einer veränderten Tabelle (wie model/editing.py)."""
    new = copy.deepcopy(diagram)
    table = new.find_table(ref)
    change(table)
    return new


# ------------------------------------------------------------------ Szene


class TestUpdateDiagram:
    def test_unchanged_diagram_keeps_all_items(self, scene, test_schema):
        entities, relations = dict(scene.entities), list(scene.relations)
        positions = scene.current_layout().positions
        assert scene.update_diagram(copy.deepcopy(test_schema))
        assert all(scene.entities[r] is item for r, item in entities.items())
        assert [id(r) for r in scene.relations] == [id(r) for r in relations]
        assert scene.current_layout().positions == positions

    def test_changed_table_keeps_box_selection_and_position(self, scene, test_schema):
        item = scene.entities[shop("artikel")]
        item.setSelected(True)
        pos = item.pos()
        height = item.rect.height()
        new = edited(test_schema, shop("artikel"), lambda t: t.columns.append(Column("preis", "decimal(10,2)")))
        assert scene.update_diagram(new)
        assert scene.entities[shop("artikel")] is item
        assert item.isSelected() and item.pos() == pos
        assert item.rect.height() > height  # neue Zeile
        assert item.table.column("preis") is not None

    def test_unaffected_relations_are_reused(self, scene, test_schema):
        before = {r.relationship.name: r for r in scene.relations}
        new = edited(test_schema, shop("artikel"), lambda t: t.columns.append(Column("preis", "int")))
        scene.update_diagram(new)
        after = {r.relationship.name: r for r in scene.relations}
        assert after.keys() == before.keys()
        assert all(after[n] is before[n] for n in after)
        # Linien bleiben mit den (unveränderten) Boxen verbunden und sind geführt
        assert all(r.points for r in scene.relations)

    def test_added_table_is_placed_without_moving_others(self, scene, test_schema):
        positions = scene.current_layout().positions
        new = add_table(test_schema, shop("lager"), Table("lager", [Column("id", "int")]))
        assert scene.update_diagram(new)
        assert shop("lager") in scene.entities
        assert scene.entities[shop("lager")] in scene.containers["shop"].members
        layout = scene.current_layout().positions
        assert all(layout[r] == p for r, p in positions.items())
        assert not scene.has_overlaps()

    def test_deleted_table_takes_its_relations_along(self, scene, test_schema):
        new = delete_table(test_schema, shop("bestellposition"))
        assert scene.update_diagram(new)
        assert shop("bestellposition") not in scene.entities
        names = {r.relationship.name for r in scene.relations}
        assert "fk_pos_bestellung" not in names and "fk_pos_artikel" not in names
        assert all(item.scene() is scene for item in scene.entities.values())
        assert all(m.ref in scene.entities for m in scene.containers["shop"].members)
        assert len(scene.items()) == len(scene.entities) + len(scene.relations) + len(list(scene.frames()))

    def test_new_and_deleted_relationship(self, scene, test_schema):
        diagram, plan = add_relationship(test_schema, shop("mitarbeiter"), shop("kunde"))
        others = {r.relationship.name: r for r in scene.relations}
        assert scene.update_diagram(diagram)
        names = [r.relationship.name for r in scene.relations]
        assert plan.fk_name in names
        assert all(r is others[r.relationship.name] for r in scene.relations if r.relationship.name in others
                   and r.relationship.child != shop("mitarbeiter"))
        assert scene.update_diagram(delete_relationship(diagram, shop("mitarbeiter"), plan.fk_name))
        assert plan.fk_name not in [r.relationship.name for r in scene.relations]

    def test_rename_keeps_box(self, scene, test_schema):
        item = scene.entities[shop("kategorie")]
        table = replace(copy.deepcopy(test_schema.find_table(shop("kategorie"))), name="gruppe")
        new = replace_table(test_schema, shop("kategorie"), table, {})
        assert scene.update_diagram(new, renamed={shop("kategorie"): shop("gruppe")})
        assert scene.entities[shop("gruppe")] is item and item.ref == shop("gruppe")
        rel = next(r for r in scene.relations if r.relationship.name == "fk_ak_kategorie")
        assert rel.parent_item is item

    def test_layout_positions_are_applied(self, scene, test_schema):
        layout = scene.current_layout()
        moved = dict(layout.positions)
        moved[shop("artikel")] = (moved[shop("artikel")][0] + 500, moved[shop("artikel")][1])
        assert scene.update_diagram(copy.deepcopy(test_schema), replace(layout, positions=moved))
        assert scene.current_layout().positions[shop("artikel")] == moved[shop("artikel")]

    def test_new_database_rebuilds(self, scene, test_schema):
        old_item = scene.entities[shop("artikel")]
        assert not scene.update_diagram(add_database(test_schema, "neu", Dialect.MYSQL))
        assert "neu" in scene.containers
        assert scene.entities[shop("artikel")] is not old_item

    def test_changed_scale_rebuilds(self, scene, test_schema):
        layout = replace(scene.current_layout(), scale=1.5)
        assert not scene.update_diagram(copy.deepcopy(test_schema), layout)
        assert scene.display_scale == 1.5

    def test_overrides_replace_only_that_line(self, scene, test_schema):
        from erd_editor.analysis import RelationshipOverride

        before = {r.relationship.name: r for r in scene.relations}
        target = before["fk_profil_kunde"].relationship
        overrides = {(target.child, target.name): RelationshipOverride(identifying=True)}
        scene.update_diagram(copy.deepcopy(test_schema), overrides=overrides)
        after = {r.relationship.name: r for r in scene.relations}
        assert after["fk_profil_kunde"] is not before["fk_profil_kunde"]
        assert after["fk_profil_kunde"].relationship.identifying
        assert all(after[n] is before[n] for n in after if n != "fk_profil_kunde")


# ------------------------------------------------------------------ Hauptfenster


def ref(table: str) -> TableRef:
    return TableRef("lager", None, table)


@pytest.fixture
def window(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from erd_editor.main import MainWindow

    def fail(*args):
        raise AssertionError(f"unerwartetes Hinweisfenster: {args[1:3]}")

    for name in ("warning", "information", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name, fail)

    w = MainWindow()
    w.create_database("lager", Dialect.MYSQL)
    pk = [Index("PRIMARY", IndexKind.PRIMARY, ("id",))]
    w.create_table(ref("regal"), Table("regal", [Column("id", "int", nullable=False)], list(pk)))
    w.create_table(ref("fach"), Table("fach", [Column("id", "int", nullable=False)], list(pk)))
    QApplication.processEvents()
    yield w
    w.setWindowModified(False)
    w.close()


def zoom_calls(window, monkeypatch) -> list:
    calls = []
    monkeypatch.setattr(window.view, "zoom_to_fit", lambda: calls.append(1))
    return calls


class TestMainWindow:
    def test_edit_keeps_box_selection_and_view(self, window, monkeypatch):
        calls = zoom_calls(window, monkeypatch)
        item = window.scene.entities[ref("regal")]
        item.setSelected(True)
        table = Table("regal", [Column("id", "int", nullable=False), Column("name", "varchar(40)")])
        window.update_table(ref("regal"), table, {})
        QApplication.processEvents()
        assert window.scene.entities[ref("regal")] is item and item.isSelected()
        assert item.table.column("name") is not None
        assert calls == []  # Bildausschnitt bleibt

    def test_undo_redo_update_in_place(self, window, monkeypatch):
        calls = zoom_calls(window, monkeypatch)
        fach = window.scene.entities[ref("fach")]
        window.create_table(ref("boden"), Table("boden", [Column("id", "int")]))
        window.undo_stack.undo()
        assert ref("boden") not in window.scene.entities
        window.undo_stack.redo()
        assert ref("boden") in window.scene.entities
        assert window.scene.entities[ref("fach")] is fach
        QApplication.processEvents()
        assert calls == []

    def test_rename_keeps_box_and_position(self, window):
        item = window.scene.entities[ref("regal")]
        pos = item.pos()
        window.update_table(ref("regal"), Table("schrank", [Column("id", "int", nullable=False)]), {})
        assert window.scene.entities[ref("schrank")] is item and item.pos() == pos
        window.undo_stack.undo()
        assert ref("regal") in window.scene.entities
        assert window.scene.entities[ref("regal")].pos() == pos

    def test_relationship_tool_keeps_boxes(self, window):
        boxes = dict(window.scene.entities)
        window.create_relationship(ref("fach"), ref("regal"))
        assert len(window.scene.relations) == 1
        # FK-Spalte kommt in „fach“ hinzu – die Box wird aktualisiert, nicht ersetzt
        assert window.scene.entities[ref("fach")] is boxes[ref("fach")]
        assert window.scene.entities[ref("fach")].table.foreign_keys
        assert window.scene.entities[ref("regal")] is boxes[ref("regal")]

    def test_switching_views_still_zooms(self, window, monkeypatch):
        window.add_subdiagram("Teil", [ref("regal")])
        calls = zoom_calls(window, monkeypatch)
        window.activate_view(None)
        QApplication.processEvents()
        assert calls == [1]
