"""Tests für Speichern/Laden und Neu-Einlesen ohne Layoutverlust."""

import copy
import json

import pytest
from PySide6.QtCore import QPointF

from erd_editor.diagram.scene import DiagramScene
from erd_editor.model import Column, ConnectionProfile, Database, Dialect, Index, IndexKind, Layout, Schema, Table, TableRef
from erd_editor.persistence.document import (
    FILE_SUFFIX,
    DiagramDocument,
    DocumentError,
    document_from_dict,
    document_to_dict,
    load_document,
    save_document,
)


def shop(table: str) -> TableRef:
    return TableRef("shop", None, table)


PROFILE = ConnectionProfile("root@localhost", Dialect.MYSQL, "localhost", 3306, "root", {"ssl": "no"})


@pytest.fixture
def document(test_schema):
    for db in test_schema.databases:
        db.connection_profile = PROFILE.name
    layout = Layout({shop("kunde"): (10.0, 20.5), shop("artikel"): (300.0, 0.0)}, {"shop": 2, "stammdaten": 0})
    return DiagramDocument(test_schema, layout, [PROFILE])


# ---------------------------------------------------------------- Dateiformat


class TestDocumentFormat:
    def test_roundtrip_dict(self, document):
        restored = document_from_dict(json.loads(json.dumps(document_to_dict(document))))
        assert restored.diagram == document.diagram
        assert restored.layout == document.layout
        assert restored.connections == document.connections

    def test_schema_and_layout_are_separate(self, document):
        data = document_to_dict(document)
        assert set(data) == {"format", "version", "connections", "schema", "layout"}
        def keys(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    yield k
                    yield from keys(v)
            elif isinstance(obj, list):
                for v in obj:
                    yield from keys(v)

        assert not {"x", "y", "color"} & set(keys(data["schema"]))  # keine Layoutdaten im Schema
        assert all({"database", "schema", "table", "x", "y"} == set(e) for e in data["layout"]["entities"])

    def test_no_password_in_file(self, document):
        assert "password" not in json.dumps(document_to_dict(document)).lower()

    def test_rejects_foreign_files(self):
        with pytest.raises(DocumentError):
            document_from_dict({"hello": "world"})
        with pytest.raises(DocumentError):
            document_from_dict([1, 2, 3])

    def test_rejects_newer_version(self, document):
        data = document_to_dict(document)
        data["version"] = 99
        with pytest.raises(DocumentError, match="99"):
            document_from_dict(data)

    def test_rejects_damaged_content(self, document):
        data = document_to_dict(document)
        del data["schema"]["databases"][0]["name"]
        with pytest.raises(DocumentError):
            document_from_dict(data)

    def test_names_with_dots(self):
        ref = TableRef("my.db", None, "a.b")
        doc = DiagramDocument(
            layout=Layout({ref: (1.0, 2.0)}),
        )
        assert document_from_dict(document_to_dict(doc)).layout.positions == {ref: (1.0, 2.0)}


class TestFiles:
    def test_save_and_load(self, document, tmp_path):
        path = tmp_path / f"test{FILE_SUFFIX}"
        save_document(document, path)
        restored = load_document(path)
        assert restored.diagram == document.diagram
        assert restored.layout == document.layout

    def test_utf8_and_readable(self, document, tmp_path):
        document.diagram.databases[0].schemas[0].tables[0].comment = "Kundenstamm äöü ß"
        path = tmp_path / f"test{FILE_SUFFIX}"
        save_document(document, path)
        text = path.read_text(encoding="utf-8")
        assert "Kundenstamm äöü ß" in text
        assert text.startswith("{\n")  # eingerückt, gut für Git-Diffs

    def test_backup_and_no_temp_files(self, document, tmp_path):
        path = tmp_path / f"test{FILE_SUFFIX}"
        save_document(document, path)
        save_document(document, path)
        names = sorted(p.name for p in tmp_path.iterdir())
        assert names == [f"test{FILE_SUFFIX}", f"test{FILE_SUFFIX}.bak"]

    def test_broken_json(self, tmp_path):
        path = tmp_path / f"kaputt{FILE_SUFFIX}"
        path.write_text("{ nicht json", encoding="utf-8")
        with pytest.raises(DocumentError):
            load_document(path)


# ---------------------------------------------------------------- Szene und Layout


@pytest.fixture
def scene(qapp):
    return DiagramScene()


class TestSceneLayout:
    def test_current_layout_roundtrip(self, scene, test_schema):
        scene.load(test_schema)
        scene.entities[shop("kunde")].setPos(1234, 567)
        layout = scene.current_layout()
        other = DiagramScene()
        other.load(test_schema, layout)
        assert other.entities[shop("kunde")].pos() == QPointF(1234, 567)
        assert other.current_layout() == layout

    def test_colors_are_kept(self, scene, test_schema):
        scene.load(test_schema, Layout(database_colors={"shop": 3, "stammdaten": 5}))
        assert scene.containers["shop"].color_index == 3
        assert scene.current_layout().database_colors == {"shop": 3, "stammdaten": 5}

    def test_new_database_gets_free_color(self, scene, test_schema):
        scene.load(test_schema, Layout(database_colors={"stammdaten": 0}))
        assert scene.containers["shop"].color_index == 1

    def test_new_table_below_existing(self, scene, test_schema):
        scene.load(test_schema)
        layout = scene.current_layout()
        test_schema.database("shop").schemas[0].tables.append(
            Table("neu", [Column("id", "int", nullable=False)], [Index("PRIMARY", IndexKind.PRIMARY, ("id",))])
        )
        scene.load(test_schema, layout)
        for ref, pos in layout.positions.items():
            assert scene.entities[ref].pos() == QPointF(*pos)  # nichts verrutscht
        new = scene.entities[shop("neu")].mapRectToScene(scene.entities[shop("neu")].rect)
        shop_items = [i for r, i in scene.entities.items() if r.database == "shop" and r.table != "neu"]
        assert new.top() > max(i.mapRectToScene(i.rect).bottom() for i in shop_items)
        assert scene.containers["shop"].rect.contains(new)

    def test_removed_table_is_ignored(self, scene, test_schema):
        scene.load(test_schema)
        layout = scene.current_layout()
        test_schema.database("shop").schemas[0].tables = [
            t for t in test_schema.database("shop").schemas[0].tables if t.name != "mitarbeiter"
        ]
        scene.load(test_schema, layout)
        assert shop("mitarbeiter") not in scene.entities
        assert shop("mitarbeiter") not in scene.current_layout().positions

    def test_new_database_placed_below(self, scene, test_schema):
        scene.load(test_schema)
        layout = scene.current_layout()
        test_schema.databases.append(Database("lager", Dialect.MYSQL, [Schema(None, [Table("regal", [Column("id", "int")])])]))
        scene.load(test_schema, layout)
        lager = scene.containers["lager"].rect
        assert lager.top() > max(c.rect.bottom() for n, c in scene.containers.items() if n != "lager")

    def test_layout_changed_only_on_user_actions(self, scene, test_schema):
        calls = []
        scene.layout_changed.connect(lambda: calls.append(1))
        scene.load(test_schema)
        scene.load(test_schema, scene.current_layout())
        assert calls == []
        scene.entities[shop("kunde")].moveBy(5, 5)
        assert calls


# ---------------------------------------------------------------- Hauptfenster


class TestMainWindow:
    @pytest.fixture
    def window(self, qapp):
        from erd_editor.main import MainWindow

        w = MainWindow()
        yield w
        w.setWindowModified(False)  # kein Nachfragen beim Aufräumen
        w.close()

    def test_save_and_open(self, window, document, tmp_path):
        window.show_document(document, use_layout=True)
        window.scene.entities[shop("kunde")].setPos(777, 333)
        path = tmp_path / f"d{FILE_SUFFIX}"
        assert window._write(path)
        assert not window.isWindowModified()
        assert window.windowTitle().startswith(path.name)

        from erd_editor.main import MainWindow

        other = MainWindow()
        assert other.open_file(path)
        assert other.scene.entities[shop("kunde")].pos() == QPointF(777, 333)
        assert other.document.connections == [PROFILE]
        other.close()

    def test_moving_marks_modified(self, window, document):
        window.show_document(document, use_layout=True)
        window.setWindowModified(False)
        window.scene.entities[shop("kunde")].moveBy(10, 0)
        assert window.isWindowModified()

    def test_open_invalid_file_fails_gracefully(self, window, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)
        path = tmp_path / f"kaputt{FILE_SUFFIX}"
        path.write_text("{}", encoding="utf-8")
        assert not window.open_file(path)

    def test_reload_keeps_positions(self, window, document, monkeypatch):
        window.show_document(document, use_layout=False)
        window.scene.entities[shop("kunde")].setPos(999, 111)

        changed = copy.deepcopy(document.diagram)
        changed.database("shop").schemas[0].tables.append(Table("neu", [Column("id", "int")]))
        calls = []

        def fake_read(profile, names):
            calls.append((profile.name, names))
            return [changed.database(n) for n in names]

        monkeypatch.setattr(window, "read_databases", fake_read)
        window.reload()
        assert calls == [(PROFILE.name, ["shop", "stammdaten"])]
        assert window.scene.entities[shop("kunde")].pos() == QPointF(999, 111)
        assert shop("neu") in window.scene.entities
        assert "1 neu" in window.statusBar().currentMessage()
        assert window.isWindowModified()

    def test_reload_aborted_keeps_everything(self, window, document, monkeypatch):
        window.show_document(document, use_layout=False)
        before = window.scene.current_layout()
        monkeypatch.setattr(window, "read_databases", lambda profile, names: None)
        window.reload()
        assert window.scene.current_layout() == before


def test_helpers():
    from erd_editor.main import describe_changes, with_suffix
    from erd_editor.model import Diagram

    assert with_suffix("C:/x/plan").name == f"plan{FILE_SUFFIX}"
    assert with_suffix(f"C:/x/plan{FILE_SUFFIX}").name == f"plan{FILE_SUFFIX}"
    a = Diagram([Database("d", Dialect.MYSQL, [Schema(None, [Table("t1"), Table("t2")])])])
    b = Diagram([Database("d", Dialect.MYSQL, [Schema(None, [Table("t2"), Table("t3"), Table("t4")])])])
    assert describe_changes(a, b) == "Neu eingelesen – 2 neu, 1 entfernt"
    assert "keine" in describe_changes(a, a)
