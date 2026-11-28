"""Notizen (Infoboxen): Speicherung, Szene, Hauptfenster."""

import copy
from dataclasses import replace

import pytest
from PySide6.QtCore import QPointF

from erd_editor.diagram.scene import DiagramScene
from erd_editor.model import Column, Dialect, Index, IndexKind, Layout, Note, Table, TableRef
from erd_editor.persistence.document import DiagramDocument, document_from_dict, document_to_dict


def shop(table: str) -> TableRef:
    return TableRef("shop", None, table)


LINKED = Note("n1", "Wird nachts befüllt", (900.0, 50.0, 200.0, 90.0), shop("kunde"))
FREE = Note("n2", "Freie Notiz", (900.0, 400.0, 160.0, 60.0))


# ------------------------------------------------------------------ Speicherung


def test_persistence_roundtrip(test_schema):
    doc = DiagramDocument(test_schema, Layout(notes=[LINKED, FREE]))
    data = document_to_dict(doc)
    assert data["layout"]["notes"][0]["entity"] == {"database": "shop", "schema": None, "table": "kunde"}
    assert document_from_dict(data).layout.notes == [LINKED, FREE]


def test_files_without_notes_stay_unchanged(test_schema):
    data = document_to_dict(DiagramDocument(test_schema, Layout()))
    assert "notes" not in data["layout"]
    assert document_from_dict(data).layout.notes == []


# ------------------------------------------------------------------ Szene


@pytest.fixture
def scene(qapp, test_schema):
    s = DiagramScene()
    s.load(test_schema)
    s.restore_layout(replace(s.current_layout(), notes=[LINKED, FREE]))
    return s


def test_notes_and_link(scene):
    assert list(scene.notes) == ["n1", "n2"]
    assert scene.current_layout().notes == [LINKED, FREE]
    assert list(scene.note_links) == ["n1"]  # nur die verbundene Notiz hat eine Linie
    link = scene.note_links["n1"]
    kunde = scene.entities[shop("kunde")]
    rect = kunde.mapRectToScene(kunde.rect)
    end = link.line.p2()
    assert rect.adjusted(-1, -1, 1, 1).contains(end) and not rect.adjusted(1, 1, -1, -1).contains(end)


def test_link_follows_entity_and_note(scene):
    link = scene.note_links["n1"]
    before = link.line
    scene.entities[shop("kunde")].moveBy(0, 200)
    assert link.line != before
    before = link.line
    scene.notes["n1"].moveBy(50, 0)
    assert link.line != before


def test_note_survives_update_and_link_disappears_with_table(scene, test_schema):
    from erd_editor.model.editing import delete_table

    for db in test_schema.databases:
        db.modeled = True
    note = scene.notes["n1"]
    new = delete_table(test_schema, shop("kunde"))
    scene.update_diagram(new, scene.current_layout())
    assert scene.notes["n1"] is note and not scene.note_links  # Notiz bleibt, Linie fällt weg
    assert scene.current_layout().notes[0].entity == shop("kunde")  # Verbindung gemerkt (Rückgängig)
    scene.update_diagram(copy.deepcopy(test_schema), scene.current_layout())
    assert "n1" in scene.note_links


def test_auto_layout_moves_linked_notes_along(scene):
    kunde, note, free = scene.entities[shop("kunde")], scene.notes["n1"], scene.notes["n2"]
    kunde.moveBy(700, 700)  # von Hand verschieben nimmt die Notiz nicht mit …
    assert note.pos() == QPointF(900, 50)
    offset = note.pos() - kunde.pos()
    free_pos = free.pos()
    scene.auto_layout()  # … Anordnen schon (gleicher Abstand)
    assert note.pos() - kunde.pos() == offset
    assert free.pos() == free_pos


def test_display_scale_scales_notes(scene):
    note = scene.notes["n2"]
    w = note.rect.width()
    scene.set_display_scale(1.5)
    assert note.rect.width() == pytest.approx(w * 1.5)
    assert note.scale_factor == 1.5
    assert note.font.pointSizeF() == pytest.approx(9.0 * 1.5)


def test_restore_layout_adds_changes_and_removes(scene):
    note = scene.notes["n1"]
    changed = replace(LINKED, text="geändert", frame=(10.0, 10.0, 300.0, 100.0))
    scene.restore_layout(replace(scene.current_layout(), notes=[changed]))
    assert list(scene.notes) == ["n1"] and scene.notes["n1"] is note
    assert note.note == changed
    assert note.scene() is scene and len([i for i in scene.items() if i.__class__.__name__ == "NoteItem"]) == 1


def test_resize_has_minimum_and_describe_change(scene):
    note = scene.notes["n2"]
    before = scene.current_layout()
    note.set_size(10, 10)
    assert note.rect.width() == 80 and note.rect.height() == 40
    assert DiagramScene.describe_change(before, scene.current_layout()) == "Notizgröße ändern"
    before = scene.current_layout()
    note.moveBy(5, 5)
    assert DiagramScene.describe_change(before, scene.current_layout()) == "Notiz verschieben"


def test_new_note_frame_next_to_entity(scene):
    from PySide6.QtCore import QRectF

    kunde = scene.entities[shop("kunde")].mapRectToScene(scene.entities[shop("kunde")].rect)
    spot = QRectF(*scene.new_note_frame(shop("kunde")))
    assert not spot.intersects(kunde)
    assert spot.adjusted(-41, -41, 41, 41).intersects(kunde)  # nah dran (erster Abstand)
    assert scene.new_note_frame(None, QPointF(5, 6))[:2] == (5, 6)


def test_notes_are_rendered_in_exports(scene):
    bounds = scene.itemsBoundingRect()
    assert bounds.contains(scene.notes["n2"].mapRectToScene(scene.notes["n2"].rect))


# ------------------------------------------------------------------ Hauptfenster


def lager(table: str) -> TableRef:
    return TableRef("lager", None, table)


@pytest.fixture
def window(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from erd_editor.main import MainWindow

    def fail(*args):
        raise AssertionError(f"unerwartetes Hinweisfenster: {args[1:3]}")

    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, fail)
    w = MainWindow()
    pk = [Index("PRIMARY", IndexKind.PRIMARY, ("id",))]
    w.create_database("lager", Dialect.MYSQL)
    w.create_table(lager("regal"), Table("regal", [Column("id", "int", nullable=False)], list(pk)))
    w.create_table(lager("fach"), Table("fach", [Column("id", "int", nullable=False)], list(pk)))
    texts = []
    monkeypatch.setattr(w, "ask_note_text", lambda current="": texts.pop(0) if texts else None)
    w.texts = texts
    yield w
    w.setWindowModified(False)
    w.close()


def test_add_note_linked_to_selection_and_undo(window):
    window.scene.entities[lager("regal")].setSelected(True)
    window.texts.append("Hält Fächer")
    note_id = window.add_note()
    note = window.scene.notes[note_id]
    assert note.text == "Hält Fächer" and note.entity == lager("regal") and note_id in window.scene.note_links
    assert window.undo_stack.undoText() == "Notiz hinzufügen"
    window.undo_stack.undo()
    assert not window.scene.notes
    window.undo_stack.redo()
    assert window.scene.notes[note_id].text == "Hält Fächer"


def test_cancelled_dialog_adds_nothing(window):
    assert window.add_note() is None  # keine Eingabe = abgebrochen
    assert not window.scene.notes


def test_edit_detach_attach_delete(window):
    note_id = window.add_note(text="alt", use_selection=False)
    window.texts.append("neu")
    window.edit_note(note_id)
    assert window.scene.notes[note_id].text == "neu"
    window.attach_note(note_id, lager("fach"))
    assert window.scene.notes[note_id].entity == lager("fach")
    window.detach_note(note_id)
    assert window.scene.notes[note_id].entity is None and not window.scene.note_links
    window.scene.clearSelection()
    window.scene.notes[note_id].setSelected(True)
    window.delete_selection()
    assert not window.scene.notes
    assert [window.undo_stack.text(i) for i in range(window.undo_stack.count())][-4:] == [
        "Notiz bearbeiten", "Notiz mit „fach“ verbinden", "Notiz-Verbindung lösen", "Notiz löschen"]


def test_rename_table_keeps_note_link(window):
    note_id = window.add_note(entity=lager("regal"), text="x")
    window.update_table(lager("regal"), Table("schrank", [Column("id", "int", nullable=False)],
                                               [Index("PRIMARY", IndexKind.PRIMARY, ("id",))]), {})
    assert window.scene.notes[note_id].entity == lager("schrank")
    assert window.scene.note_links[note_id].entity_item is window.scene.entities[lager("schrank")]


def test_notes_belong_to_their_view(window):
    note_id = window.add_note(text="nur Gesamt", use_selection=False)
    window.add_subdiagram("Teil", [lager("regal")])
    assert not window.scene.notes  # Teildiagramm hat eigene Notizen
    window.add_note(text="nur Teil", use_selection=False)
    window.activate_view(None)
    assert [n.text for n in window.scene.notes.values()] == ["nur Gesamt"] and note_id in window.scene.notes
    window.activate_view("Teil")
    assert [n.text for n in window.scene.notes.values()] == ["nur Teil"]


def test_context_menus(window):
    window.scene.notes.clear()
    note_id = window.add_note(text="n", use_selection=False)

    def actions(menu):
        return {a.text(): a for a in menu.actions() if a.text()}

    window.scene.clearSelection()
    window.scene.entities[lager("fach")].setSelected(True)
    note_actions = actions(window.build_note_menu(note_id))
    assert "Notiz bearbeiten …" in note_actions
    assert not any("lösen" in text for text in note_actions)  # noch nicht verbunden
    connect = actions(note_actions["Verbinden mit"].menu())
    assert list(connect) == ["Markiert: fach", "fach", "regal"]
    connect["Markiert: fach"].trigger()
    assert window.scene.notes[note_id].entity == lager("fach")

    window.texts.append("angeheftet")
    actions(window.build_entity_menu(lager("regal")))["Notiz anheften …"].trigger()
    assert any(n.entity == lager("regal") and n.text == "angeheftet" for n in window.scene.notes.values())

    window.texts.append("hier")
    actions(window.build_canvas_menu(QPointF(-500, -500)))["Notiz hier einfügen …"].trigger()
    here = next(n for n in window.scene.notes.values() if n.text == "hier")
    assert here.entity is None and here.pos() == QPointF(-500, -500)


def menu_actions(menu):
    return {a.text(): a for a in menu.actions() if a.text()}


def test_connect_from_note_without_selection(window):
    """Rechtsklick auf die Notiz reicht – keine Tabelle muss vorher markiert sein."""
    note_id = window.add_note(text="n", use_selection=False)
    window.scene.clearSelection()
    connect = menu_actions(menu_actions(window.build_note_menu(note_id))["Verbinden mit"].menu())
    assert list(connect) == ["fach", "regal"]
    assert not any(a.isChecked() for a in connect.values())
    connect["regal"].trigger()
    assert window.scene.notes[note_id].entity == lager("regal")
    assert window.undo_stack.text(window.undo_stack.index() - 1) == "Notiz mit „regal“ verbinden"

    # danach: aktuelle Verbindung angehakt, erneutes Wählen ändert nichts
    menu = menu_actions(window.build_note_menu(note_id))
    assert "Verbindung zu „regal“ lösen" in menu
    connect = menu_actions(menu["Verbinden mit"].menu())
    assert connect["regal"].isChecked() and not connect["fach"].isChecked()
    steps = window.undo_stack.count()
    connect["regal"].trigger()
    assert connect["regal"].isChecked() and window.undo_stack.count() == steps
    connect["fach"].trigger()
    assert window.scene.notes[note_id].entity == lager("fach")


def test_connect_menu_full_names_for_duplicates(window):
    window.create_database("archiv", Dialect.MYSQL)
    window.create_table(TableRef("archiv", None, "fach"),
                        Table("fach", [Column("id", "int", nullable=False)], [Index("PRIMARY", IndexKind.PRIMARY, ("id",))]))
    note_id = window.add_note(text="n", use_selection=False)
    connect = menu_actions(menu_actions(window.build_note_menu(note_id))["Verbinden mit"].menu())
    assert list(connect) == ["archiv.fach", "lager.fach", "regal"]


def test_connect_from_table_menu(window):
    window.scene.clearSelection()
    sub = menu_actions(window.build_entity_menu(lager("regal")))["Notiz verbinden"].menu()
    assert [a.text() for a in sub.actions()] == ["(keine Notizen in dieser Ansicht)"]
    assert not sub.actions()[0].isEnabled()

    first = window.add_note(text="Zweite Zeile folgt\nmehr Text", use_selection=False)
    second = window.add_note(entity=lager("fach"), text="an fach " + "x" * 60)
    window.scene.clearSelection()
    window.scene.notes[first].setSelected(True)
    notes = menu_actions(menu_actions(window.build_entity_menu(lager("regal")))["Notiz verbinden"].menu())
    labels = list(notes)
    assert labels[0] == "Markiert: Zweite Zeile folgt"
    assert labels[1].startswith("an fach xxx") and "…" in labels[1] and labels[1].endswith("(bisher: fach)")
    assert labels[2] == "Zweite Zeile folgt"
    notes[labels[1]].trigger()
    assert window.scene.notes[second].entity == lager("regal")


def test_connect_menu_empty_view(window):
    window.add_subdiagram("Leer", [])
    window.add_note(text="n", use_selection=False)
    note_id = next(iter(window.scene.notes))
    sub = menu_actions(window.build_note_menu(note_id))["Verbinden mit"].menu()
    assert [a.text() for a in sub.actions()] == ["(keine Tabellen in dieser Ansicht)"]


def test_save_and_open_keeps_notes(window, tmp_path):
    window.add_note(entity=lager("fach"), text="gespeichert")
    from erd_editor.persistence.document import FILE_SUFFIX

    path = tmp_path / f"notizen{FILE_SUFFIX}"
    window._write(path)
    window.open_file(path)
    notes = list(window.scene.notes.values())
    assert [(n.text, n.entity) for n in notes] == [("gespeichert", lager("fach"))]
    assert notes[0].id in window.scene.note_links


def test_new_note_avoids_boxes_and_notes(scene):
    from PySide6.QtCore import QRectF

    for ref in [r for r, _ in scene.current_layout().positions.items()][:6]:
        x, y, w, h = scene.new_note_frame(ref)
        spot = QRectF(x, y, w, h)
        assert not any(spot.intersects(e.mapRectToScene(e.rect)) for e in scene.entities.values())
        assert not any(spot.intersects(n.mapRectToScene(n.rect)) for n in scene.notes.values())
