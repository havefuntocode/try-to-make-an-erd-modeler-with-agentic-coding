"""Gemerkte Orte der Datei-Dialoge: letzter Ordner je Zweck, „Zuletzt geöffnet“."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox

from erd_editor.ui import recent


class TestSettings:
    def test_last_dir_per_purpose(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        assert recent.last_dir(recent.DIAGRAM) is None
        recent.remember_dir(recent.DIAGRAM, a / "x.erd.json")
        recent.remember_dir(recent.EXPORT, b / "x.pdf")
        assert recent.last_dir(recent.DIAGRAM) == a.resolve()
        assert recent.last_dir(recent.EXPORT) == b.resolve()
        assert recent.last_dir(recent.SQL) is None

    def test_vanished_dir_is_ignored(self, tmp_path):
        gone = tmp_path / "gone"
        gone.mkdir()
        recent.remember_dir(recent.DIAGRAM, gone / "x.erd.json")
        gone.rmdir()
        assert recent.last_dir(recent.DIAGRAM) is None

    def test_recent_files_order_dedup_limit(self, tmp_path):
        first = tmp_path / "eins.erd.json"
        recent.add_recent_file(first)
        assert recent.recent_files() == [first.resolve()]  # ein einzelner Eintrag bleibt eine Liste
        recent.add_recent_file(tmp_path / "zwei.erd.json")
        recent.add_recent_file(str(first).upper())  # gleiche Datei, andere Schreibweise
        names = [p.name.lower() for p in recent.recent_files()]
        assert names == ["eins.erd.json", "zwei.erd.json"]
        for i in range(20):
            recent.add_recent_file(tmp_path / f"d{i}.erd.json")
        files = recent.recent_files()
        assert len(files) == recent.MAX_RECENT
        assert files[0].name == "d19.erd.json"

    def test_remove_and_clear(self, tmp_path):
        recent.add_recent_file(tmp_path / "a.erd.json")
        recent.add_recent_file(tmp_path / "b.erd.json")
        recent.remove_recent_file(tmp_path / "a.erd.json")
        assert [p.name for p in recent.recent_files()] == ["b.erd.json"]
        recent.clear_recent_files()
        assert recent.recent_files() == []


@pytest.fixture
def window(qapp, test_schema):
    from erd_editor.main import MainWindow

    w = MainWindow()
    w.load_diagram(test_schema)
    yield w
    w.setWindowModified(False)
    w.close()


class TestMainWindow:
    def test_save_remembers_folder_and_file(self, window, tmp_path, monkeypatch):
        target = tmp_path / "ablage" / "shop.erd.json"
        target.parent.mkdir()
        assert window._write(target)
        assert recent.last_dir(recent.DIAGRAM) == target.parent.resolve()
        assert recent.recent_files() == [target.resolve()]

        # Öffnen-Dialog startet im gemerkten Ordner
        seen = {}

        def fake_open(_parent, _title, start, _filter):
            seen["start"] = start
            return "", ""

        monkeypatch.setattr(QFileDialog, "getOpenFileName", fake_open)
        window.path = None
        window.open_dialog()
        assert seen["start"] == str(target.parent.resolve())

    def test_recent_menu_opens_file(self, window, tmp_path):
        target = tmp_path / "shop.erd.json"
        assert window._write(target)
        window.path = None
        window._fill_recent_menu()
        actions = [a for a in window.recent_menu.actions() if not a.isSeparator()]
        assert "shop.erd.json" in actions[0].text()
        assert actions[-1].text() == "Liste &leeren"
        actions[0].trigger()
        assert window.path == target.resolve()

    def test_empty_recent_menu(self, window):
        window._fill_recent_menu()
        actions = window.recent_menu.actions()
        assert len(actions) == 1 and not actions[0].isEnabled()

    def test_missing_recent_file_is_removed(self, window, tmp_path, monkeypatch):
        missing = tmp_path / "weg.erd.json"
        recent.add_recent_file(missing)
        warnings = []
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))
        assert not window.open_recent(missing)
        assert warnings and recent.recent_files() == []

    def test_export_uses_last_export_folder(self, window, tmp_path):
        window.path = tmp_path / "shop.erd.json"
        assert window._export_path(".pdf") == tmp_path / "shop.pdf"
        out = tmp_path / "exporte"
        out.mkdir()
        recent.remember_dir(recent.EXPORT, out / "irgendwas.png")
        assert window._export_path(".pdf") == out.resolve() / "shop.pdf"

    def test_sql_dialog_remembers_folder(self, qapp, test_schema, tmp_path):
        from erd_editor.ui.sql_dialog import SqlExportDialog

        dialog = SqlExportDialog(test_schema, start_dir=tmp_path)
        assert dialog.suggested_path().parent == tmp_path
        out = tmp_path / "skripte"
        out.mkdir()
        dialog.save(str(out / "shop.sql"))
        assert recent.last_dir(recent.SQL) == out.resolve()
        assert SqlExportDialog(test_schema, start_dir=tmp_path).suggested_path().parent == out.resolve()
