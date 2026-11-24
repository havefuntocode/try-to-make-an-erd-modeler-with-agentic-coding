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
