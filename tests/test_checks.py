"""CHECK-Constraints: Ausdrucks-Hilfen, Speichern, Bearbeiten."""

import pytest

from erd_editor.model import CheckConstraint, Column, Dialect, Table, TableRef
from erd_editor.model.checks import expression_columns, rename_in_expression, strip_outer_parens
from erd_editor.model.editing import add_database, add_table, delete_relationship, replace_table
from erd_editor.model.schema import Diagram, ForeignKey
from erd_editor.persistence.document import _table_from_dict, _table_to_dict

COLS = ["preis", "rabatt", "status", "Beginn", "ende"]


class TestExpressions:
    @pytest.mark.parametrize(
        ("expression", "columns"),
        [
            ("preis > 0", ("preis",)),
            ("[preis]>(0)", ("preis",)),
            ("`preis` > 0 AND `rabatt` < `preis`", ("preis", "rabatt")),
            ('"ende" >= "beginn"', ("ende", "Beginn")),
            ("status IN ('preis', 'offen')", ("status",)),  # Zeichenkette ist keine Spalte
            ("(preis > (0)::numeric)", ("preis",)),
            ("json_valid(status)", ("status",)),
            ("len(status) > 0", ("status",)),
            ("x::preis > 0", ()),  # Typ-Cast, keine Spalte
            ("upper(ende) = 'X''preis'", ("ende",)),  # verdoppeltes Hochkomma
        ],
    )
    def test_expression_columns(self, expression, columns):
        assert expression_columns(expression, COLS) == columns

    def test_function_named_like_column_is_not_a_column(self):
        assert expression_columns("status(preis) > 0", COLS) == ("preis",)

    def test_rename_keeps_quoting(self):
        assert rename_in_expression("`preis` > 0 AND preis < 'preis'", {"preis": "betrag"}) == (
            "`betrag` > 0 AND betrag < 'preis'"
        )
        assert rename_in_expression("[preis]>(0)", {"PREIS": "net"}) == "[net]>(0)"
        assert rename_in_expression('"a""b" > 0', {'a"b': 'c"d'}) == '"c""d" > 0'

    @pytest.mark.parametrize(
        ("raw", "clean"),
        [
            ("((preis > (0)::numeric))", "preis > (0)::numeric"),
            ("([preis]>(0))", "[preis]>(0)"),
            ("(a > 0) AND (b > 0)", "(a > 0) AND (b > 0)"),
            ("(status <> ')')", "status <> ')'"),
            ("preis > 0", "preis > 0"),
        ],
    )
    def test_strip_outer_parens(self, raw, clean):
        assert strip_outer_parens(raw) == clean


def table_with_checks() -> Table:
    return Table(
        "artikel",
        [Column("id", "int", False), Column("preis", "decimal(10,2)"), Column("rabatt", "decimal(10,2)")],
        checks=[
            CheckConstraint("artikel_preis_check", "preis > 0", ("preis",)),
            CheckConstraint("artikel_rabatt_check", "rabatt < preis", ("rabatt", "preis")),
        ],
    )


def test_column_checks():
    t = table_with_checks()
    assert [c.name for c in t.column_checks("preis")] == ["artikel_preis_check"]
    assert t.column_checks("rabatt") == []


def test_persistence_roundtrip():
    t = table_with_checks()
    assert _table_from_dict(_table_to_dict(t)).checks == t.checks
    assert "checks" not in _table_to_dict(Table("leer", [Column("id", "int")]))


@pytest.fixture
def model():
    d = add_database(Diagram(), "lager", Dialect.MYSQL)
    d = add_table(d, TableRef("lager", None, "artikel"), table_with_checks())
    return d


REF = TableRef("lager", None, "artikel")


def test_rename_column_rewrites_checks(model):
    t = model.find_table(REF)
    cols = [Column("betrag" if c.name == "preis" else c.name, c.data_type) for c in t.columns]
    d = replace_table(model, REF, Table("artikel", cols, checks=t.checks), {"preis": "betrag"})
    assert [(c.expression, c.columns) for c in d.find_table(REF).checks] == [
        ("betrag > 0", ("betrag",)),
        ("rabatt < betrag", ("rabatt", "betrag")),
    ]


def test_removed_column_drops_checks(model):
    t = model.find_table(REF)
    cols = [c for c in t.columns if c.name != "rabatt"]
    d = replace_table(model, REF, Table("artikel", cols, checks=t.checks))
    assert [c.name for c in d.find_table(REF).checks] == ["artikel_preis_check"]
    assert d.validate() == []
    d.find_table(REF).checks[0].columns = ("weg",)
    assert "unbekannte Spalte 'weg'" in d.validate()[0]


def test_delete_relationship_drops_checks_of_dropped_columns(model):
    d = add_table(model, TableRef("lager", None, "lager"), Table("lager", [Column("id", "int", False)]))
    t = d.find_table(REF)
    t.columns.append(Column("lager_id", "int"))
    t.foreign_keys.append(ForeignKey("fk_lager", ("lager_id",), TableRef("lager", None, "lager"), ("id",)))
    t.checks.append(CheckConstraint("ck_lager", "lager_id > 0", ("lager_id",)))
    d = delete_relationship(d, REF, "fk_lager")
    assert [c.name for c in d.find_table(REF).checks] == ["artikel_preis_check", "artikel_rabatt_check"]


class TestTableDialog:
    @pytest.fixture
    def dialog(self, qapp):
        from erd_editor.ui.table_dialog import TableDialog

        t = table_with_checks()
        t.columns.append(Column("ende", "date"))
        t.checks.append(CheckConstraint("ck_ende", "ende > '2000-01-01'", ("ende",)))
        t.checks.append(CheckConstraint("ck_ende2", "ende < '2100-01-01'", ("ende",)))  # zweiter Check derselben Spalte
        return TableDialog(Dialect.MYSQL, t)

    def test_load_splits_column_and_table_checks(self, dialog):
        from erd_editor.ui.table_dialog import CHECK, CK_COLUMNS, CK_EXPRESSION, CK_NAME

        assert [dialog._text(r, CHECK) for r in range(4)] == ["", "preis > 0", "", "ende > '2000-01-01'"]
        assert [(dialog._ck_text(r, CK_NAME), dialog._ck_text(r, CK_EXPRESSION), dialog._ck_text(r, CK_COLUMNS))
                for r in range(dialog.ck_grid.rowCount())] == [
            ("artikel_rabatt_check", "rabatt < preis", "rabatt, preis"),
            ("ck_ende2", "ende < '2100-01-01'", "ende"),
        ]
        assert dialog.message.text() == ""
        assert dialog.table().checks == [
            CheckConstraint("artikel_preis_check", "preis > 0", ("preis",)),
            CheckConstraint("ck_ende", "ende > '2000-01-01'", ("ende",)),
            CheckConstraint("artikel_rabatt_check", "rabatt < preis", ("rabatt", "preis")),
            CheckConstraint("ck_ende2", "ende < '2100-01-01'", ("ende",)),
        ]

    def test_new_column_check_gets_name(self, dialog):
        from erd_editor.ui.table_dialog import CHECK

        dialog.grid.item(0, CHECK).setText("id > 0")
        check = dialog.table().checks[0]
        assert (check.name, check.expression, check.columns) == ("artikel_id_check", "id > 0", ("id",))

    def test_rename_column_rewrites_expressions(self, dialog):
        from erd_editor.ui.table_dialog import CHECK, CK_COLUMNS, CK_EXPRESSION, NAME

        dialog.grid.item(1, NAME).setText("betrag")
        assert dialog._text(1, CHECK) == "betrag > 0"
        assert dialog._ck_text(0, CK_EXPRESSION) == "rabatt < betrag"
        assert dialog._ck_text(0, CK_COLUMNS) == "rabatt, betrag"
        assert dialog.column_renames() == {"preis": "betrag"}

    def test_move_keeps_checks(self, dialog):
        from erd_editor.ui.table_dialog import CHECK

        dialog.grid.setCurrentCell(1, 0)
        dialog.move_row(-1)
        assert dialog._text(0, CHECK) == "preis > 0"
        assert dialog.table().checks[0].name == "artikel_preis_check"

    def test_remove_column_drops_table_checks(self, dialog):
        dialog.grid.setCurrentCell(2, 0)  # rabatt
        dialog.remove_row()
        assert [c.name for c in dialog.table().checks] == ["artikel_preis_check", "ck_ende", "ck_ende2"]

    def test_tab_validation(self, dialog):
        from erd_editor.ui.table_dialog import CK_EXPRESSION, CK_NAME

        row = dialog.add_check()
        assert dialog._ck_text(row, CK_NAME) == "artikel_check"
        assert "braucht einen Ausdruck" in dialog.message.text()
        dialog.ck_grid.item(row, CK_EXPRESSION).setText("preis >= rabatt")
        assert dialog.message.text() == ""
        dialog.ck_grid.item(row, CK_NAME).setText("CK_ENDE")
        assert "eindeutig" in dialog.message.text()
        dialog.ck_grid.item(row, CK_NAME).setText("")
        assert "braucht einen Namen" in dialog.message.text()

    def test_edit_in_model_via_replace_table(self, dialog, model):
        from erd_editor.ui.table_dialog import NAME

        dialog.grid.item(1, NAME).setText("betrag")
        t = dialog.table()
        t.columns = [c for c in t.columns if c.name != "ende"]
        t.checks = [c for c in t.checks if "ende" not in c.columns]
        d = replace_table(model, REF, t, dialog.column_renames(), final_indexes=True)
        assert d.validate() == []
        assert d.find_table(REF).checks[0].expression == "betrag > 0"


def test_column_tooltip_lists_checks():
    from erd_editor.diagram.entity_item import column_tooltip

    t = table_with_checks()
    assert "Check: preis &gt; 0<br>Check: rabatt &lt; preis" in column_tooltip(t.column("preis"), t)
    assert "Check" not in column_tooltip(t.column("id"), t)


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("CHECK ((preis > (0)::numeric)) NOT VALID", "preis > (0)::numeric"),
        ("CHECK (id > 0)", "id > 0"),
        ("([preis]>=(0))", "[preis]>=(0)"),
        ("(`status` in (_utf8mb4'offen',_utf8mb4'erledigt'))", "`status` in ('offen','erledigt')"),
        ("json_valid(`inhalt`)", "json_valid(`inhalt`)"),
        ("_rabatt > 0", "_rabatt > 0"),  # Spalte mit Unterstrich ist kein Zeichensatz-Präfix
    ],
)
def test_clean_check_expression(raw, clean):
    from erd_editor.model.checks import clean_check_expression

    assert clean_check_expression(raw) == clean


def _rows(**base):
    return [dict(base)]


def test_build_database_reads_checks_in_all_adapters():
    from erd_editor.adapters import mssql, mysql, postgres

    col = {"column_name": "preis", "nullable": 0, "is_nullable": "NO", "column_default": None, "comment": None}
    my = mysql.build_database(
        "shop",
        tables=[{"table_name": "artikel", "comment": None}],
        columns=[{**col, "table_name": "artikel", "column_type": "decimal(10,2)", "extra": ""}],
        indexes=[], foreign_keys=[],
        checks=[{"table_name": "artikel", "constraint_name": "artikel_chk_1",
                 "expression": "(`status` in (_utf8mb4\'offen\'))"},
                {"table_name": "artikel", "constraint_name": "ck_preis", "expression": "(`preis` > 0)"}],
        server_version="8.4.0",
    )
    t = my.schemas[0].tables[0]
    assert [(c.name, c.expression, c.columns) for c in t.checks] == [
        ("artikel_chk_1", "`status` in ('offen')", ()),
        ("ck_preis", "`preis` > 0", ("preis",)),
    ]

    pg = postgres.build_database(
        "shop",
        schemas=[{"schema_name": "public"}],
        tables=[{"schema_name": "public", "table_name": "artikel", "comment": None, "kind": "r"}],
        columns=[{**col, "schema_name": "public", "table_name": "artikel", "data_type": "numeric",
                  "identity": "", "generated": ""}],
        indexes=[], foreign_keys=[],
        checks=[{"schema_name": "public", "table_name": "artikel", "constraint_name": "artikel_preis_check",
                 "expression": "CHECK ((preis > (0)::numeric))"}],
    )
    assert pg.schemas[0].tables[0].checks[0].expression == "preis > (0)::numeric"
    assert pg.schemas[0].tables[0].checks[0].columns == ("preis",)

    ms = mssql.build_database(
        "shop",
        schemas=[{"schema_name": "dbo"}],
        tables=[{"schema_name": "dbo", "table_name": "artikel", "comment": None, "kind": "U"}],
        columns=[{**col, "schema_name": "dbo", "table_name": "artikel", "type_name": "decimal", "max_length": 9,
                  "precision": 10, "scale": 2, "is_user_type": 0, "is_identity": 0}],
        indexes=[], foreign_keys=[],
        checks=[{"schema_name": "dbo", "table_name": "artikel", "constraint_name": "CK_x", "expression": "([preis]>(0))"}],
    )
    assert [(c.expression, c.columns) for c in ms.schemas[0].tables[0].checks] == [("[preis]>(0)", ("preis",))]


# ---------------------------------------------------------------- Darstellung in der Box


def test_column_check_icon():
    from erd_editor.diagram.markers import CheckIcon, column_check_icon

    t = table_with_checks()
    assert [column_check_icon(t, c) for c in t.columns] == [None, CheckIcon.CHECK, CheckIcon.CHECK_PART]


class TestBox:
    def box(self, table, **options):
        from erd_editor.diagram.entity_item import EntityItem
        from erd_editor.model import DisplayOptions

        return EntityItem(REF, table, options=DisplayOptions(**options))

    def test_symbol_needs_room_only_with_checks(self, qapp):
        t = table_with_checks()
        plain = Table(t.name, t.columns)
        with_checks, without = self.box(t), self.box(plain)
        assert [r.check is not None for r in with_checks._columns] == [False, True, True]
        assert without._check_w == 0
        assert with_checks._check_w > 0
        # Symbol liegt hinter den Datentypen und innerhalb der Box
        s = with_checks.style
        assert with_checks._check_x + s.marker_size <= with_checks.rect.width() - s.padding

    def test_checks_in_index_area_only_when_switched_on(self, qapp):
        from PySide6.QtCore import QPointF

        t = table_with_checks()
        assert self.box(t, show_indexes=False)._index_top is None
        box = self.box(t, show_indexes=False, show_checks=True)
        assert [(r.kind, r.label) for r in box._indexes] == [
            ("CHECK", "artikel_preis_check: preis > 0"),
            ("CHECK", "artikel_rabatt_check: rabatt < preis"),
        ]
        tip = box.tooltip_at(QPointF(10, box._indexes[1].top + 2))
        assert "CHECK artikel_rabatt_check" in tip and "Spalten: rabatt, preis" in tip

    def test_long_expression_is_elided(self, qapp):
        t = table_with_checks()
        t.checks[0].expression = "preis > 0 AND " * 40 + "preis < 1"
        box = self.box(t, show_checks=True)
        label = next(r.label for r in box._indexes if r.check is t.checks[0])
        assert label.endswith("…")
        tip_y = next(r.top for r in box._indexes if r.check is t.checks[0]) + 2
        from PySide6.QtCore import QPointF

        assert "preis &lt; 1" in box.tooltip_at(QPointF(10, tip_y))  # Tooltip zeigt den ganzen Ausdruck

    def test_paint(self, qapp):
        from PySide6.QtGui import QImage, QPainter

        image = QImage(400, 300, QImage.Format.Format_ARGB32)
        painter = QPainter(image)
        self.box(table_with_checks(), show_checks=True).paint(painter, None)
        painter.end()


def test_show_checks_is_saved_and_in_menu(qapp, model):
    from erd_editor.main import MainWindow
    from erd_editor.model import DisplayOptions, Layout
    from erd_editor.persistence.document import _layout_from_dict, _layout_to_dict

    layout = Layout(display=DisplayOptions(show_checks=True))
    assert _layout_from_dict(_layout_to_dict(layout)).display.show_checks
    assert not _layout_from_dict({}).display.show_checks  # ältere Dateien: aus

    w = MainWindow()
    w.load_diagram(model)
    w.option_actions["show_checks"].setChecked(True)
    assert w.scene.display_options.show_checks
    w.setWindowModified(False)
    w.close()
