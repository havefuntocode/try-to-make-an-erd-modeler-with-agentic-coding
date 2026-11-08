"""Tests für Teildiagramme: Modell, Datei, Dialog, Reiter, Rückgängig."""

import copy

import pytest
from PySide6.QtCore import QPointF, Qt

from erd_editor.model import Column, ColumnMode, ConnectionProfile, Dialect, DisplayOptions, Table, TableRef
from erd_editor.model.subdiagram import Subdiagram, existing_tables, filter_diagram, neighbours, unique_name
from erd_editor.persistence.document import FILE_SUFFIX, DiagramDocument, document_from_dict, document_to_dict
from erd_editor.ui.subdiagram_dialog import SubdiagramDialog


def shop(table: str) -> TableRef:
    return TableRef("shop", None, table)


ORDER = [shop("kunde"), shop("bestellung"), shop("bestellposition")]


# ---------------------------------------------------------------- Modell


class TestModel:
    def test_filter_keeps_only_chosen_tables(self, test_schema):
        sub = filter_diagram(test_schema, ORDER)
        assert {r for r, _ in sub.tables()} == set(ORDER)
        assert [db.name for db in sub.databases] == ["shop"]  # stammdaten entfällt
        assert sub.find_table(shop("kunde")) is test_schema.find_table(shop("kunde"))  # geteilt, nicht kopiert

    def test_filter_keeps_schema_level(self, schema_diagram):
        sub = filter_diagram(schema_diagram, [TableRef("erd", "stammdaten", "land")])
        assert [s.name for s in sub.database("erd").schemas] == ["stammdaten"]

    def test_neighbours(self, test_schema):
        found = neighbours(test_schema, {shop("bestellung")})
        assert found == {shop("kunde"), shop("adresse"), shop("bestellposition")}

    def test_existing_tables(self, test_schema):
        assert existing_tables(test_schema, [shop("kunde"), shop("weg")]) == [shop("kunde")]

    def test_unique_name(self):
        assert unique_name("A", set()) == "A"
        assert unique_name("A", {"A", "A 2"}) == "A 3"

    def test_file_roundtrip(self, test_schema):
        from erd_editor.model import Layout

        doc = DiagramDocument(test_schema, subdiagrams=[
            Subdiagram("Bestellwesen", ORDER, Layout({shop("kunde"): (1.0, 2.0)}, display=DisplayOptions(ColumnMode.KEYS))),
            Subdiagram("Neu", [shop("artikel")], None),
        ])
        restored = document_from_dict(document_to_dict(doc))
        assert [s.name for s in restored.subdiagrams] == ["Bestellwesen", "Neu"]
        assert set(restored.subdiagrams[0].tables) == set(ORDER)
        assert restored.subdiagrams[0].layout.display.columns is ColumnMode.KEYS
        assert restored.subdiagrams[1].layout is None

    def test_old_files_have_no_subdiagrams(self):
        assert document_from_dict({"format": "erd-editor", "version": 1}).subdiagrams == []


# ---------------------------------------------------------------- Dialog


class TestDialog:
    def test_validation(self, qapp, test_schema):
        d = SubdiagramDialog(test_schema, "", [], {"Bestellwesen"})
        assert "Namen" in d.message.text()
        d.name.setText("Bestellwesen")
        assert "gibt es schon" in d.message.text()
        d.name.setText("Gesamt")
        assert "gibt es schon" in d.message.text()
        d.name.setText("Personal")
        assert "Tabelle" in d.message.text()
        d._items[shop("mitarbeiter")].setCheckState(0, Qt.CheckState.Checked)
        assert d.message.text() == ""

    def test_editing_keeps_own_name(self, qapp, test_schema):
        d = SubdiagramDialog(test_schema, "Bestellwesen", ORDER, {"Bestellwesen"})
        assert d.message.text() == ""

    def test_neighbours_and_filter(self, qapp, test_schema):
        d = SubdiagramDialog(test_schema, "X", [shop("bestellung")])
        d.add_neighbours()
        assert set(d.selected_tables()) == {shop("bestellung"), shop("kunde"), shop("adresse"), shop("bestellposition")}
        d.filter.setText("kat")
        d._set_all(True)  # wirkt nur auf sichtbare
        assert shop("kategorie") in d.selected_tables() and shop("artikel") not in d.selected_tables()


# ---------------------------------------------------------------- Hauptfenster


@pytest.fixture
def window(qapp, test_schema):
    from erd_editor.main import MainWindow

    w = MainWindow()
    w.load_diagram(test_schema)
    yield w
    w.setWindowModified(False)
    w.close()


def entity_refs(w):
    return set(w.scene.entities)


class TestTabs:
    def test_tabs_hidden_without_subdiagrams(self, window):
        assert window.tabs.isHidden()
        assert window.tabs.count() == 1

    def test_add_switch_and_lines(self, window):
        window.add_subdiagram("Bestellwesen", ORDER)
        assert window.current_view == "Bestellwesen"
        assert not window.tabs.isHidden() and window.tabs.tabText(1) == "Bestellwesen"
        assert entity_refs(window) == set(ORDER)
        assert {r.relationship.name for r in window.scene.relations} == {"fk_bestellung_kunde", "fk_pos_bestellung"}
        window.tabs.setCurrentIndex(0)
        assert window.current_view is None and len(window.scene.entities) == 10

    def test_layouts_are_independent(self, window):
        window.scene.entities[shop("kunde")].setPos(5000, 5000)
        window.add_subdiagram("Bestellwesen", ORDER)
        assert window.scene.entities[shop("kunde")].pos() != QPointF(5000, 5000)  # eigenes Auto-Layout
        window.scene.entities[shop("kunde")].setPos(-300, 0)
        window.activate_view(None)
        assert window.scene.entities[shop("kunde")].pos() == QPointF(5000, 5000)
        window.activate_view("Bestellwesen")
        assert window.scene.entities[shop("kunde")].pos() == QPointF(-300, 0)

    def test_colors_match_main_view(self, window):
        main = {n: c.color_index for n, c in window.scene.containers.items()}
        window.add_subdiagram("Stamm", [TableRef("stammdaten", None, "land")])
        assert window.scene.containers["stammdaten"].color_index == main["stammdaten"]

    def test_new_view_inherits_size_and_options(self, window):
        window.set_display_scale(1.5)
        window.column_actions[ColumnMode.KEYS].trigger()
        window.add_subdiagram("Bestellwesen", ORDER)
        assert window.scene.display_scale == 1.5
        assert window.scene.display_options.columns is ColumnMode.KEYS

    def test_from_selection(self, window):
        for ref in ORDER:
            window.scene.entities[ref].setSelected(True)
        window.new_subdiagram(from_selection=True)
        assert window.current_view == "Teildiagramm"
        assert entity_refs(window) == set(ORDER)

    def test_from_empty_selection_does_nothing(self, window):
        window.new_subdiagram(from_selection=True)
        assert window.document.subdiagrams == []
        assert "markieren" in window.statusBar().currentMessage()

    def test_step_view(self, window):
        window.add_subdiagram("A", ORDER)
        window.add_subdiagram("B", [shop("artikel")])
        window.activate_view(None)
        window.step_view(+1)
        assert window.current_view == "A"
        window.step_view(-1)
        window.step_view(-1)
        assert window.current_view == "B"

    def test_footer_names_view(self, window):
        window.add_subdiagram("Bestellwesen", ORDER)
        assert "· Bestellwesen ·" in window.footer_text()


class TestEditDeleteUndo:
    def test_edit_via_dialog(self, window, monkeypatch):
        window.add_subdiagram("Bestellwesen", ORDER)

        def fake_exec(dialog):
            dialog.name.setText("Verkauf")
            dialog._items[shop("artikel")].setCheckState(0, Qt.CheckState.Checked)
            return SubdiagramDialog.DialogCode.Accepted

        monkeypatch.setattr(SubdiagramDialog, "exec", fake_exec)
        window.edit_subdiagram("Bestellwesen")
        assert window.current_view == "Verkauf"
        assert shop("artikel") in window.scene.entities
        window.undo_stack.undo()
        assert window.document.subdiagram("Bestellwesen") is not None
        assert shop("artikel") not in window.scene.entities

    def test_delete_and_undo(self, window, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
        window.add_subdiagram("Bestellwesen", ORDER)
        window.scene.entities[shop("kunde")].setPos(777, 0)
        window.delete_subdiagram("Bestellwesen")
        assert window.document.subdiagrams == [] and window.current_view is None
        window.undo_stack.undo()
        assert window.current_view == "Bestellwesen"
        assert window.scene.entities[shop("kunde")].pos() == QPointF(777, 0)  # Layout zurück

    def test_main_view_cannot_be_deleted(self, window):
        window.delete_subdiagram(None)
        assert "nicht gelöscht" in window.statusBar().currentMessage()

    def test_undo_switches_to_view_of_change(self, window):
        window.add_subdiagram("Bestellwesen", ORDER)
        window._auto_layout()  # schon angeordnet → kein Schritt
        window.set_display_scale(1.5)  # Schritt in „Bestellwesen“
        window.activate_view(None)
        window.undo_stack.undo()
        assert window.current_view == "Bestellwesen"
        assert window.scene.display_scale == 1.0

    def test_undo_add_subdiagram(self, window):
        window.add_subdiagram("Bestellwesen", ORDER)
        window.undo_stack.undo()
        assert window.document.subdiagrams == [] and window.current_view is None
        window.undo_stack.redo()
        assert window.current_view == "Bestellwesen"


class TestSaveAndReload:
    def test_save_and_open_with_views(self, window, tmp_path):
        from erd_editor.main import MainWindow

        window.add_subdiagram("Bestellwesen", ORDER)
        window.scene.entities[shop("kunde")].setPos(123, 456)
        path = tmp_path / f"d{FILE_SUFFIX}"
        window._write(path)
        other = MainWindow()
        other.open_file(path)
        assert other.current_view is None and other.tabs.count() == 2
        other.activate_view("Bestellwesen")
        assert other.scene.entities[shop("kunde")].pos() == QPointF(123, 456)
        other.close()

    def test_reload_prunes_and_keeps_views(self, window, test_schema, monkeypatch):
        profile = ConnectionProfile("p", Dialect.MYSQL, "h")
        for db in window.document.diagram.databases:
            db.connection_profile = "p"
        window.document.connections = [profile]
        window.add_subdiagram("Bestellwesen", ORDER + [shop("mitarbeiter")])
        changed = copy.deepcopy(test_schema)
        shop_tables = changed.database("shop").schemas[0].tables
        changed.database("shop").schemas[0].tables = [t for t in shop_tables if t.name != "mitarbeiter"]
        shop_tables = changed.database("shop").schemas[0].tables
        shop_tables.append(Table("neu", [Column("id", "int")]))
        monkeypatch.setattr(window, "read_databases", lambda p, names, *a: [changed.database(n) for n in names])

        window.reload()
        assert window.current_view == "Bestellwesen"
        assert entity_refs(window) == set(ORDER)  # mitarbeiter entfernt, neu nicht hinzugefügt
        window.activate_view(None)
        assert shop("neu") in window.scene.entities
