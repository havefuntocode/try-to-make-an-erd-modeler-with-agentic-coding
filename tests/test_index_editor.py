"""Tests für das Bearbeiten von Indexen im Tabelleneditor (Version 3)."""

import pytest
from PySide6.QtCore import Qt

from erd_editor.model import Column, Dialect, Index, IndexKind, Table, TableRef
from erd_editor.model.editing import EditError, add_database, add_table, replace_table
from erd_editor.model.schema import Diagram
from erd_editor.ui.table_dialog import IDX_COLUMNS, IDX_KIND, IDX_NAME, NAME, TableDialog

REF = TableRef("lager", None, "artikel")


def artikel() -> Table:
    return Table(
        "artikel",
        [Column("id", "int", nullable=False), Column("name", "varchar(50)"), Column("ean", "char(13)")],
        [Index("PRIMARY", IndexKind.PRIMARY, ("id",)), Index("uq_ean", IndexKind.UNIQUE, ("ean",)),
         Index("idx_name_ean", IndexKind.INDEX, ("name", "ean"))],
    )


def diagram() -> Diagram:
    return add_table(add_database(Diagram(), "lager", Dialect.MYSQL), REF, artikel())


def kinds(d: TableDialog) -> list[str]:
    combo = d.idx_grid.cellWidget(0, IDX_KIND)
    return [combo.itemText(i) for i in range(combo.count())]


class TestDialog:
    def test_existing_indexes_are_listed(self, qapp):
        d = TableDialog(Dialect.MYSQL, artikel())
        assert [i.name for i in d._index_rows()] == ["uq_ean", "idx_name_ean"]
        assert d.idx_grid.item(1, IDX_COLUMNS).text() == "name, ean"
        assert d.table().indexes == artikel().indexes

    def test_kinds_per_dialect(self, qapp):
        d = TableDialog(Dialect.MYSQL, artikel())
        assert "FULLTEXT" in kinds(d)
        d = TableDialog(Dialect.POSTGRESQL, artikel())
        assert kinds(d) == ["UNIQUE", "INDEX"]

    def test_add_index_with_selected_column(self, qapp):
        d = TableDialog(Dialect.MYSQL)
        d.name.setText("kunde")
        row = d.add_row(Column("email", "varchar(100)"))
        d.grid.setCurrentCell(row, NAME)
        d.add_index()
        combo = d.idx_grid.cellWidget(0, IDX_KIND)
        combo.setCurrentIndex(combo.findData(IndexKind.UNIQUE))
        assert d.table().indexes[1] == Index("idx_kunde", IndexKind.UNIQUE, ("email",))
        assert d.message.text() == ""

    def test_pick_and_order_columns(self, qapp):
        d = TableDialog(Dialect.MYSQL, artikel())
        d.idx_grid.setCurrentCell(1, IDX_NAME)  # idx_name_ean
        labels = [d.idx_columns.item(i).text() for i in range(d.idx_columns.count())]
        assert labels == ["name", "ean", "id"]
        d.idx_columns.setCurrentRow(1)
        d.move_index_column(-1)  # ean vor name
        d.idx_columns.item(2).setCheckState(Qt.CheckState.Checked)  # id dazu
        assert d._index_rows()[1].columns == ("ean", "name", "id")

    def test_rename_column_follows_into_index(self, qapp):
        d = TableDialog(Dialect.MYSQL, artikel())
        d.grid.item(2, NAME).setText("gtin")
        assert d.idx_grid.item(0, IDX_COLUMNS).text() == "gtin"
        assert d._index_rows()[0].columns == ("gtin",)

    def test_remove_column_shrinks_or_drops_index(self, qapp):
        d = TableDialog(Dialect.MYSQL, artikel())
        d.grid.setCurrentCell(2, NAME)  # ean
        d.remove_row()
        assert [(i.name, i.columns) for i in d._index_rows()] == [("idx_name_ean", ("name",))]

    def test_move_row_keeps_index_columns(self, qapp):
        d = TableDialog(Dialect.MYSQL, artikel())
        d.grid.setCurrentCell(2, NAME)
        d.move_row(-1)
        assert d._index_rows()[1].columns == ("name", "ean")

    def test_validation(self, qapp):
        d = TableDialog(Dialect.MYSQL, artikel())
        d.idx_grid.item(1, IDX_NAME).setText("uq_ean")
        assert "eindeutig" in d.message.text()
        d.idx_grid.item(1, IDX_NAME).setText("PRIMARY")
        assert "Primärschlüssel" in d.message.text()
        d.idx_grid.item(1, IDX_NAME).setText("")
        assert "Namen" in d.message.text()
        d.idx_grid.item(1, IDX_NAME).setText("idx_x")
        d.idx_grid.setCurrentCell(1, IDX_NAME)
        for i in range(d.idx_columns.count()):
            d.idx_columns.item(i).setCheckState(Qt.CheckState.Unchecked)
        assert "mindestens eine Spalte" in d.message.text()

    def test_remove_index(self, qapp):
        d = TableDialog(Dialect.MYSQL, artikel())
        d.idx_grid.setCurrentCell(0, IDX_NAME)
        d.remove_index()
        assert [i.name for i in d._index_rows()] == ["idx_name_ean"]


class TestReplaceTable:
    def test_final_indexes_are_not_renamed_twice(self, qapp):
        # name → bezeichnung, neue Spalte heißt wieder „name“ und bekommt einen Index
        d = TableDialog(Dialect.MYSQL, artikel())
        d.grid.item(1, NAME).setText("bezeichnung")
        row = d.add_row(Column("name", "varchar(20)"))
        d.grid.setCurrentCell(row, NAME)
        d.add_index("idx_kurz")
        new = replace_table(diagram(), REF, d.table(), d.column_renames(), final_indexes=True)
        t = new.find_table(REF)
        assert {i.name: i.columns for i in t.indexes}["idx_kurz"] == ("name",)
        assert {i.name: i.columns for i in t.indexes}["idx_name_ean"] == ("bezeichnung", "ean")
        assert new.validate() == []

    def test_final_indexes_with_unknown_column_refused(self):
        t = artikel()
        t.indexes.append(Index("idx_x", IndexKind.INDEX, ("gibts_nicht",)))
        with pytest.raises(EditError, match="unbekannte Spalten"):
            replace_table(diagram(), REF, t, final_indexes=True)

    def test_window_edit_updates_indexes_with_undo(self, qapp, monkeypatch):
        from erd_editor.main import MainWindow

        w = MainWindow()
        try:
            w.create_database("lager", Dialect.MYSQL)
            w.create_table(REF, artikel())

            def fake_exec(dialog):
                dialog.idx_grid.setCurrentCell(0, IDX_NAME)
                dialog.remove_index()
                return TableDialog.DialogCode.Accepted

            monkeypatch.setattr(TableDialog, "exec", fake_exec)
            w.edit_table(REF)
            assert [i.name for i in w.document.diagram.find_table(REF).indexes] == ["PRIMARY", "idx_name_ean"]
            w.undo_stack.undo()
            assert "uq_ean" in [i.name for i in w.document.diagram.find_table(REF).indexes]
        finally:
            w.setWindowModified(False)
            w.close()
