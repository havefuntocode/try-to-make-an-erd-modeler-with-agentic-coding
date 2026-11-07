"""Tests für die Ableitung von Beziehungen und Kardinalitäten."""

import pytest

from erd_editor.analysis import (
    EXACTLY_ONE,
    ZERO_OR_MANY,
    ZERO_OR_ONE,
    Cardinality,
    Relationship,
    child_cardinality,
    derive_relationships,
    is_identifying,
    is_junction_table,
)
from erd_editor.model import (
    Column,
    Database,
    Diagram,
    Dialect,
    ForeignKey,
    Index,
    IndexKind,
    Schema,
    Table,
    TableRef,
)


def shop(table: str) -> TableRef:
    return TableRef("shop", None, table)


@pytest.fixture
def rels(test_schema) -> dict[str, Relationship]:
    return {r.name: r for r in derive_relationships(test_schema)}


def test_schema_is_valid(test_schema):
    assert test_schema.validate() == []


def test_every_foreign_key_becomes_a_relationship(test_schema, rels):
    fk_count = sum(len(t.foreign_keys) for _, t in test_schema.tables())
    assert len(rels) == fk_count == 11


class TestCardinality:
    def test_str(self):
        assert str(ZERO_OR_ONE) == "0..1"
        assert str(EXACTLY_ONE) == "1..1"
        assert str(ZERO_OR_MANY) == "0..*"

    def test_flags(self):
        assert ZERO_OR_MANY.is_many and ZERO_OR_MANY.is_optional
        assert not EXACTLY_ONE.is_optional and not EXACTLY_ONE.is_many
        assert Cardinality(1, None).is_many


class TestIdentifying:
    def test_weak_entity(self, rels):
        assert rels["fk_pos_bestellung"].identifying
        assert rels["fk_adresse_kunde"].identifying

    def test_fk_outside_pk(self, rels):
        assert not rels["fk_pos_artikel"].identifying
        assert not rels["fk_bestellung_kunde"].identifying

    def test_junction_table_has_two_identifying(self, rels):
        assert rels["fk_ak_artikel"].identifying
        assert rels["fk_ak_kategorie"].identifying

    def test_table_without_pk_is_never_identifying(self):
        t = Table("log", [Column("x", "int")], foreign_keys=[ForeignKey("fk", ("x",), shop("a"), ("id",))])

        assert not is_identifying(t, t.foreign_keys[0])


class TestParentEnd:
    def test_not_null_fk_is_mandatory(self, rels):
        assert rels["fk_bestellung_kunde"].parent_end == EXACTLY_ONE

    def test_nullable_fk_is_optional(self, rels):
        assert rels["fk_kunde_land"].parent_end == ZERO_OR_ONE
        assert rels["fk_vorgesetzter"].parent_end == ZERO_OR_ONE

    def test_multi_column_fk_with_nullable_column(self, rels):
        assert rels["fk_lieferadresse"].parent_end == ZERO_OR_ONE


class TestChildEnd:
    def test_one_to_many(self, rels):
        assert rels["fk_bestellung_kunde"].child_end == ZERO_OR_MANY
        assert not rels["fk_bestellung_kunde"].is_one_to_one

    def test_unique_fk_is_one_to_one(self, rels):
        assert rels["fk_profil_kunde"].child_end == ZERO_OR_ONE
        assert rels["fk_profil_kunde"].is_one_to_one

    def test_composite_pk_containing_fk_is_one_to_many(self, rels):
        # (bestellung_id, pos) ist eindeutig, bestellung_id allein nicht
        assert rels["fk_pos_bestellung"].child_end == ZERO_OR_MANY

    def test_fk_equal_to_pk_is_one_to_one(self):
        t = Table(
            "kunde_extra",
            [Column("kunde_id", "int", nullable=False)],
            [Index("PRIMARY", IndexKind.PRIMARY, ("kunde_id",))],
            [ForeignKey("fk", ("kunde_id",), shop("kunde"), ("id",))],
        )

        assert child_cardinality(t, t.foreign_keys[0]) == ZERO_OR_ONE
        assert is_identifying(t, t.foreign_keys[0])

    def test_unique_on_subset_of_fk_is_one_to_one(self):
        t = Table(
            "t",
            [Column("a", "int"), Column("b", "int")],
            [Index("uq_a", IndexKind.UNIQUE, ("a",))],
            [ForeignKey("fk", ("a", "b"), shop("x"), ("a", "b"))],
        )

        assert child_cardinality(t, t.foreign_keys[0]) == ZERO_OR_ONE

    def test_fulltext_does_not_make_unique(self):
        t = Table(
            "t",
            [Column("a", "int")],
            [Index("ft", IndexKind.FULLTEXT, ("a",))],
            [ForeignKey("fk", ("a",), shop("x"), ("a",))],
        )

        assert child_cardinality(t, t.foreign_keys[0]) == ZERO_OR_MANY


class TestSpecialCases:
    def test_self_reference(self, rels):
        r = rels["fk_vorgesetzter"]
        assert r.is_self_reference
        assert r.child == r.parent == shop("mitarbeiter")

    def test_cross_database(self, rels):
        r = rels["fk_kunde_land"]
        assert r.is_cross_database
        assert r.parent == TableRef("stammdaten", None, "land")
        assert not rels["fk_bestellung_kunde"].is_cross_database

    def test_parallel_relationships_are_numbered(self, rels):
        liefer, rechnung = rels["fk_lieferadresse"], rels["fk_rechnungsadresse"]
        assert (liefer.parallel_index, liefer.parallel_count) == (0, 2)
        assert (rechnung.parallel_index, rechnung.parallel_count) == (1, 2)

    def test_single_relationship_is_not_parallel(self, rels):
        r = rels["fk_profil_kunde"]
        assert (r.parallel_index, r.parallel_count) == (0, 1)

    def test_parallel_ignores_direction(self):
        """FKs A→B und B→A zählen zum selben Tabellenpaar."""

        a = Table("a", [Column("id", "int"), Column("b_id", "int")],
                  [Index("PRIMARY", IndexKind.PRIMARY, ("id",))],
                  [ForeignKey("fk_a_b", ("b_id",), shop("b"), ("id",))])
        b = Table("b", [Column("id", "int"), Column("a_id", "int")],
                  [Index("PRIMARY", IndexKind.PRIMARY, ("id",))],
                  [ForeignKey("fk_b_a", ("a_id",), shop("a"), ("id",))])
        d = Diagram([Database("shop", Dialect.MYSQL, [Schema(None, [a, b])])])
        assert [r.parallel_count for r in derive_relationships(d)] == [2, 2]

    def test_missing_target_is_skipped(self, test_schema):
        test_schema.databases.pop()  # "stammdaten" entfernen
        names = {r.name for r in derive_relationships(test_schema)}
        assert "fk_kunde_land" not in names
        assert len(names) == 10


class TestJunctionTable:
    def test_detects_junction(self, test_schema):
        assert is_junction_table(test_schema.find_table(shop("artikel_kategorie")))

    def test_weak_entity_is_not_junction(self, test_schema):
        assert not is_junction_table(test_schema.find_table(shop("bestellposition")))

    def test_normal_table_is_not_junction(self, test_schema):
        assert not is_junction_table(test_schema.find_table(shop("bestellung")))
