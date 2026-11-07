"""Tests für das neutrale Datenmodell."""

import pytest

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


def ref(table: str, database: str = "shop") -> TableRef:
    return TableRef(database, None, table)


@pytest.fixture
def diagram() -> Diagram:
    """Kleiner Ausschnitt des Testschemas (MySQL, zwei Datenbanken)."""
    kunde = Table(
        name="kunde",
        comment="Kundenstamm",
        columns=[
            Column("id", "int unsigned", nullable=False, auto_increment=True),
            Column("name", "varchar(100)", nullable=False),
            Column("land_code", "char(2)", nullable=True),
        ],
        indexes=[
            Index("PRIMARY", IndexKind.PRIMARY, ("id",)),
            Index("ft_name", IndexKind.FULLTEXT, ("name",)),
        ],
        foreign_keys=[
            # datenbankübergreifender FK
            ForeignKey("fk_kunde_land", ("land_code",), ref("land", "stammdaten"), ("code",)),
        ],
    )
    bestellung = Table(
        name="bestellung",
        columns=[
            Column("id", "int unsigned", nullable=False),
            Column("kunde_id", "int unsigned", nullable=False),
        ],
        indexes=[
            Index("PRIMARY", IndexKind.PRIMARY, ("id",)),
            Index("ix_kunde", IndexKind.INDEX, ("kunde_id",)),
        ],
        foreign_keys=[ForeignKey("fk_bestellung_kunde", ("kunde_id",), ref("kunde"), ("id",))],
    )
    bestellposition = Table(
        name="bestellposition",
        columns=[
            Column("bestellung_id", "int unsigned", nullable=False),
            Column("pos", "smallint", nullable=False),
            Column("menge", "int", nullable=False, default="1"),
        ],
        indexes=[Index("PRIMARY", IndexKind.PRIMARY, ("bestellung_id", "pos"))],
        foreign_keys=[
            ForeignKey("fk_pos_bestellung", ("bestellung_id",), ref("bestellung"), ("id",), on_delete="CASCADE")
        ],
    )
    mitarbeiter = Table(
        name="mitarbeiter",
        columns=[
            Column("id", "int", nullable=False),
            Column("vorgesetzter_id", "int", nullable=True),
        ],
        indexes=[Index("PRIMARY", IndexKind.PRIMARY, ("id",))],
        foreign_keys=[ForeignKey("fk_vorgesetzter", ("vorgesetzter_id",), ref("mitarbeiter"), ("id",))],
    )
    land = Table(
        name="land",
        columns=[Column("code", "char(2)", nullable=False)],
        indexes=[Index("PRIMARY", IndexKind.PRIMARY, ("code",))],
    )
    return Diagram(
        databases=[
            Database(
                "shop",
                Dialect.MYSQL,
                schemas=[Schema(None, [kunde, bestellung, bestellposition, mitarbeiter])],
                charset="utf8mb4",
            ),
            Database("stammdaten", Dialect.MYSQL, schemas=[Schema(None, [land])]),
        ]
    )


class TestTableRef:
    def test_key_without_schema(self):
        assert ref("kunde").key == "shop.kunde"

    def test_key_with_schema(self):
        assert TableRef("shop", "dbo", "kunde").key == "shop.dbo.kunde"

    def test_is_hashable_and_comparable(self):
        assert {ref("a"), ref("a")} == {ref("a")}
        assert ref("a") != TableRef("shop", "public", "a")


class TestForeignKey:
    def test_column_count_must_match(self):
        with pytest.raises(ValueError):
            ForeignKey("fk", ("a", "b"), ref("x"), ("a",))

    def test_needs_columns(self):
        with pytest.raises(ValueError):
            ForeignKey("fk", (), ref("x"), ())


class TestTable:
    def test_column_flags(self, diagram):
        pos = diagram.find_table(ref("bestellposition"))
        assert pos.primary_key_columns == ("bestellung_id", "pos")
        assert pos.is_primary_key_column("bestellung_id")
        assert pos.is_foreign_key_column("bestellung_id")
        assert not pos.is_foreign_key_column("pos")
        assert not pos.is_indexed_column("menge")

    def test_indexed_column(self, diagram):
        kunde = diagram.find_table(ref("kunde"))
        assert kunde.is_indexed_column("name")
        assert not kunde.is_indexed_column("land_code")

    def test_unique_index_ignores_column_order(self, diagram):
        pos = diagram.find_table(ref("bestellposition"))
        assert pos.has_unique_index_on(("pos", "bestellung_id"))
        assert not pos.has_unique_index_on(("bestellung_id",))

    def test_fulltext_is_not_unique(self, diagram):
        kunde = diagram.find_table(ref("kunde"))
        assert not kunde.has_unique_index_on(("name",))

    def test_table_without_primary_key(self):
        t = Table("log", columns=[Column("text", "text")])
        assert t.primary_key is None
        assert t.primary_key_columns == ()


class TestDiagram:
    def test_tables_yields_all_refs(self, diagram):
        refs = {r.key for r, _ in diagram.tables()}
        assert refs == {
            "shop.kunde",
            "shop.bestellung",
            "shop.bestellposition",
            "shop.mitarbeiter",
            "stammdaten.land",
        }

    def test_find_table_across_databases(self, diagram):
        assert diagram.find_table(ref("land", "stammdaten")).name == "land"
        assert diagram.find_table(ref("land")) is None
        assert diagram.find_table(TableRef("fehlt", None, "x")) is None

    def test_valid_diagram_has_no_problems(self, diagram):
        assert diagram.validate() == []

    def test_validate_missing_target_table(self, diagram):
        diagram.databases.pop()  # Datenbank "stammdaten" entfernen
        problems = diagram.validate()
        assert len(problems) == 1
        assert "fehlende Tabelle stammdaten.land" in problems[0]

    def test_validate_unknown_columns(self, diagram):
        kunde = diagram.find_table(ref("kunde"))
        kunde.indexes.append(Index("ix_x", IndexKind.INDEX, ("gibts_nicht",)))
        kunde.foreign_keys.append(ForeignKey("fk_x", ("auch_nicht",), ref("bestellung"), ("nope",)))
        problems = diagram.validate()
        assert len(problems) == 3

    def test_validate_duplicate_primary_key(self, diagram):
        kunde = diagram.find_table(ref("kunde"))
        kunde.indexes.append(Index("PRIMARY2", IndexKind.PRIMARY, ("name",)))
        assert any("mehr als ein Primärschlüssel" in p for p in diagram.validate())
