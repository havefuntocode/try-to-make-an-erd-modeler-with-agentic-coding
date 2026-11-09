"""Tests für die Bearbeitungsoperationen des Modells (Version 3)."""

import pytest

from erd_editor.model import Column, Diagram, Dialect, ForeignKey, Index, IndexKind, Table, TableRef
from erd_editor.model.editing import (
    EditError,
    add_database,
    add_table,
    delete_table,
    replace_table,
    take_over_as_model,
)
from erd_editor.persistence.document import DiagramDocument, document_from_dict, document_to_dict


def shop(table: str) -> TableRef:
    return TableRef("shop", None, table)


@pytest.fixture
def model(test_schema):
    return take_over_as_model(test_schema, "shop")


def test_add_database_per_dialect():
    d = add_database(Diagram(), "lager", Dialect.MYSQL)
    d = add_database(d, "pg", Dialect.POSTGRESQL)
    d = add_database(d, "ms", Dialect.MSSQL, "verkauf")
    assert [s.name for s in d.database("lager").schemas] == [None]
    assert [s.name for s in d.database("pg").schemas] == ["public"]
    assert [s.name for s in d.database("ms").schemas] == ["verkauf"]
    assert all(db.modeled for db in d.databases)
    with pytest.raises(EditError):
        add_database(d, "lager", Dialect.MYSQL)
    with pytest.raises(EditError):
        add_database(d, "  ", Dialect.MYSQL)


def test_operations_do_not_change_original(model):
    before = document_to_dict(DiagramDocument(model))
    add_table(model, shop("neu"), Table("neu", [Column("id", "int")]))
    delete_table(model, shop("kunde"))
    assert document_to_dict(DiagramDocument(model)) == before


def test_read_databases_are_read_only(test_schema):
    with pytest.raises(EditError, match="schreibgeschützt"):
        add_table(test_schema, shop("neu"), Table("neu"))
    taken = take_over_as_model(test_schema, "shop")
    assert taken.database("shop").modeled and taken.database("shop").connection_profile is None


def test_add_table(model):
    d = add_table(model, shop("lager"), Table("x", [Column("id", "int")]))
    assert d.find_table(shop("lager")).name == "lager"  # Name kommt aus dem Verweis
    with pytest.raises(EditError):
        add_table(d, shop("lager"), Table("lager"))


def test_rename_table_updates_foreign_keys(model):
    kunde = model.find_table(shop("kunde"))
    renamed = Table("customer", kunde.columns, kunde.indexes, kunde.foreign_keys, kunde.comment)
    d = replace_table(model, shop("kunde"), renamed)
    assert d.find_table(shop("kunde")) is None
    targets = {fk.name: fk.referenced_table for _, t in d.tables() for fk in t.foreign_keys}
    assert targets["fk_bestellung_kunde"] == shop("customer")
    assert targets["fk_profil_kunde"] == shop("customer")
    assert d.validate() == []


def test_rename_column_updates_everything(model):
    kunde = model.find_table(shop("kunde"))
    cols = [Column("kunden_nr" if c.name == "id" else c.name, c.data_type, c.nullable) for c in kunde.columns]
    edited = Table("kunde", cols, kunde.indexes, kunde.foreign_keys, kunde.comment)
    d = replace_table(model, shop("kunde"), edited, {"id": "kunden_nr"})
    assert d.find_table(shop("kunde")).primary_key_columns == ("kunden_nr",)
    fk = next(fk for fk in d.find_table(shop("bestellung")).foreign_keys if fk.name == "fk_bestellung_kunde")
    assert fk.referenced_columns == ("kunden_nr",)
    assert d.validate() == []


def test_rename_own_fk_column(model):
    pos = model.find_table(shop("bestellposition"))
    cols = [Column("bestell_id" if c.name == "bestellung_id" else c.name, c.data_type, c.nullable) for c in pos.columns]
    d = replace_table(model, shop("bestellposition"), Table("bestellposition", cols, pos.indexes, pos.foreign_keys),
                      {"bestellung_id": "bestell_id"})
    t = d.find_table(shop("bestellposition"))
    assert t.primary_key_columns == ("bestell_id", "pos")
    assert next(fk for fk in t.foreign_keys if fk.name == "fk_pos_bestellung").columns == ("bestell_id",)


def test_removed_column_drops_dependents(model):
    kunde = model.find_table(shop("kunde"))
    cols = [c for c in kunde.columns if c.name != "id"]
    d = replace_table(model, shop("kunde"), Table("kunde", cols, kunde.indexes, kunde.foreign_keys))
    assert d.find_table(shop("kunde")).primary_key is None
    # FKs, die auf kunde.id zeigten, entfallen
    names = {fk.name for _, t in d.tables() for fk in t.foreign_keys}
    assert "fk_bestellung_kunde" not in names and "fk_profil_kunde" not in names
    assert d.validate() == []


def test_self_reference_follows_rename(model):
    m = model.find_table(shop("mitarbeiter"))
    d = replace_table(model, shop("mitarbeiter"), Table("person", m.columns, m.indexes, m.foreign_keys))
    (fk,) = d.find_table(shop("person")).foreign_keys
    assert fk.referenced_table == shop("person")


def test_duplicate_column_names(model):
    with pytest.raises(EditError, match="eindeutig"):
        replace_table(model, shop("artikel"), Table("artikel", [Column("a", "int"), Column("a", "int")]))


def test_rename_to_existing_table(model):
    a = model.find_table(shop("artikel"))
    with pytest.raises(EditError):
        replace_table(model, shop("artikel"), Table("kunde", a.columns))


def test_delete_table(model):
    d = delete_table(model, shop("kunde"))
    assert d.find_table(shop("kunde")) is None
    assert all(fk.referenced_table != shop("kunde") for _, t in d.tables() for fk in t.foreign_keys)
    assert d.validate() == []


def test_modeled_flag_saved():
    d = add_database(Diagram(), "lager", Dialect.MYSQL)
    assert document_from_dict(document_to_dict(DiagramDocument(d))).diagram.database("lager").modeled


# ---------------------------------------------------------------- Beziehungen

from erd_editor.analysis import derive_relationships  # noqa: E402
from erd_editor.model.editing import add_relationship, delete_relationship, plan_relationship  # noqa: E402


def rel(diagram, name):
    return next(r for r in derive_relationships(diagram) if r.name == name)


class TestRelationships:
    def test_one_to_many(self, model):
        d, plan = add_relationship(model, shop("artikel"), shop("kunde"), mandatory=False)
        assert [(c.name, c.data_type, c.nullable) for c in plan.columns] == [("kunde_id", "int", True)]
        r = rel(d, plan.fk_name)
        assert (r.child, r.parent, str(r.parent_end), str(r.child_end), r.identifying) == (
            shop("artikel"), shop("kunde"), "0..1", "0..*", False)
        assert d.validate() == []

    def test_mandatory(self, model):
        d, plan = add_relationship(model, shop("artikel"), shop("kunde"))
        assert str(rel(d, plan.fk_name).parent_end) == "1..1"

    def test_one_to_one(self, model):
        d, plan = add_relationship(model, shop("artikel"), shop("kunde"), one_to_one=True)
        assert plan.unique_index == "uq_artikel_kunde"
        assert rel(d, plan.fk_name).is_one_to_one

    def test_identifying_extends_primary_key(self, model):
        d, plan = add_relationship(model, shop("artikel"), shop("kategorie"), identifying=True)
        assert d.find_table(shop("artikel")).primary_key_columns == ("id", "kategorie_id")
        r = rel(d, plan.fk_name)
        assert r.identifying and not r.is_one_to_one

    def test_identifying_one_to_one_without_pk(self, model):
        d = add_table(model, shop("profil"), Table("profil", [Column("text", "text")]))
        d, plan = add_relationship(d, shop("profil"), shop("kunde"), one_to_one=True, identifying=True)
        assert d.find_table(shop("profil")).primary_key_columns == ("kunde_id",)
        r = rel(d, plan.fk_name)
        assert r.identifying and r.is_one_to_one

    def test_composite_parent_key(self, model):
        d, plan = add_relationship(model, shop("artikel"), shop("adresse"))
        assert [c.name for c in plan.columns] == ["kunde_id", "nr"]
        assert plan.referenced_columns == ("kunde_id", "nr")
        assert d.validate() == []

    def test_name_conflicts_are_resolved(self, model):
        d, plan = add_relationship(model, shop("bestellung"), shop("kunde"))
        assert [c.name for c in plan.columns] == ["kunde_id_2"]  # kunde_id gibt es schon
        assert plan.fk_name == "fk_bestellung_kunde_2"  # fk_bestellung_kunde gibt es schon
        assert d.validate() == []

    def test_self_reference(self, model):
        d, plan = add_relationship(model, shop("kategorie"), shop("kategorie"), mandatory=False)
        assert [c.name for c in plan.columns] == ["kategorie_id"]
        assert rel(d, plan.fk_name).is_self_reference
        with pytest.raises(EditError, match="Selbstreferenz"):
            plan_relationship(model, shop("kategorie"), shop("kategorie"), identifying=True)

    def test_refusals(self, model, test_schema):
        with pytest.raises(EditError, match="innerhalb einer Datenbank"):
            plan_relationship(model, shop("kunde"), TableRef("stammdaten", None, "land"))
        with pytest.raises(EditError, match="schreibgeschützt"):
            plan_relationship(test_schema, shop("artikel"), shop("kunde"))
        no_pk = add_table(model, shop("log"), Table("log", [Column("text", "text")]))
        with pytest.raises(EditError, match="keinen Primärschlüssel"):
            plan_relationship(no_pk, shop("artikel"), shop("log"))

    def test_plan_changes_nothing(self, model):
        before = document_to_dict(DiagramDocument(model))
        plan_relationship(model, shop("artikel"), shop("kunde"))
        assert document_to_dict(DiagramDocument(model)) == before

    def test_delete_relationship_with_columns(self, model):
        d, plan = add_relationship(model, shop("artikel"), shop("kategorie"), one_to_one=True, identifying=True)
        d = delete_relationship(d, shop("artikel"), plan.fk_name)
        t = d.find_table(shop("artikel"))
        assert t.column("kategorie_id") is None
        assert t.primary_key_columns == ("id",)
        assert all(i.name != plan.unique_index for i in t.indexes)
        assert d.validate() == []

    def test_delete_relationship_keep_columns(self, model):
        d = delete_relationship(model, shop("bestellung"), "fk_bestellung_kunde", drop_columns=False)
        t = d.find_table(shop("bestellung"))
        assert t.column("kunde_id") is not None
        assert all(fk.name != "fk_bestellung_kunde" for fk in t.foreign_keys)

    def test_delete_keeps_columns_used_by_other_fk(self, model):
        # zweiter FK über dieselbe Spalte kunde_id
        d = _clone_with_second_fk(model)
        d = delete_relationship(d, shop("bestellung"), "fk_bestellung_kunde")
        assert d.find_table(shop("bestellung")).column("kunde_id") is not None


def _clone_with_second_fk(diagram):
    import copy

    from erd_editor.model import ForeignKey

    d = copy.deepcopy(diagram)
    d.find_table(shop("bestellung")).foreign_keys.append(ForeignKey("fk_zwei", ("kunde_id",), shop("kunde"), ("id",)))
    return d
