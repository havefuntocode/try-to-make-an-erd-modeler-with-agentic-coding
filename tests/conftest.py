"""Gemeinsame Fixtures: das Testschema aus PROJEKT.md, Abschnitt 11, als neutrales Modell."""

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


def shop(table: str) -> TableRef:
    return TableRef("shop", None, table)


def pk(*columns: str) -> Index:
    return Index("PRIMARY", IndexKind.PRIMARY, columns)


def col(name: str, data_type: str = "int", nullable: bool = False) -> Column:
    return Column(name, data_type, nullable=nullable)


def build_test_schema(shop_db: str = "shop", master_db: str = "stammdaten") -> Diagram:
    """Testschema mit allen Sonderfällen (MySQL, zwei Datenbanken).

    Die Datenbanknamen sind wählbar, damit der Live-Test gegen
    `erd_test_shop`/`erd_test_stammdaten` (tests/schema/mysql.sql) vergleichen kann.
    """

    def ref(table: str) -> TableRef:
        return TableRef(shop_db, None, table)

    land = Table("land", [col("code", "char(2)"), col("name", "varchar(80)")], [pk("code")])

    kunde = Table(
        "kunde",
        [col("id"), col("name", "varchar(100)"), col("land_code", "char(2)", nullable=True)],
        [pk("id"), Index("ft_name", IndexKind.FULLTEXT, ("name",))],
        # NULL-fähiger, datenbankübergreifender FK
        [ForeignKey("fk_kunde_land", ("land_code",), TableRef(master_db, None, "land"), ("code",))],
        comment="Kundenstamm",
    )
    # 1:1 über UNIQUE-FK
    kundenprofil = Table(
        "kundenprofil",
        [col("id"), col("kunde_id"), col("bio", "text", nullable=True)],
        [pk("id"), Index("uq_kunde", IndexKind.UNIQUE, ("kunde_id",))],
        [ForeignKey("fk_profil_kunde", ("kunde_id",), ref("kunde"), ("id",))],
    )
    # Ziel eines mehrspaltigen FK
    adresse = Table(
        "adresse",
        [col("kunde_id"), col("nr", "smallint"), col("ort", "varchar(80)")],
        [pk("kunde_id", "nr")],
        [ForeignKey("fk_adresse_kunde", ("kunde_id",), ref("kunde"), ("id",))],
    )
    # NOT-NULL-FK, mehrspaltiger FK, zwei FKs auf dieselbe Tabelle
    bestellung = Table(
        "bestellung",
        [
            col("id"),
            col("kunde_id"),
            col("liefer_kunde_id", nullable=True),
            col("liefer_nr", "smallint", nullable=True),
            col("rechnung_kunde_id", nullable=True),
            col("rechnung_nr", "smallint", nullable=True),
        ],
        [pk("id"), Index("ix_kunde", IndexKind.INDEX, ("kunde_id",))],
        [
            ForeignKey("fk_bestellung_kunde", ("kunde_id",), ref("kunde"), ("id",)),
            ForeignKey(
                "fk_lieferadresse", ("liefer_kunde_id", "liefer_nr"), ref("adresse"), ("kunde_id", "nr")
            ),
            ForeignKey(
                "fk_rechnungsadresse",
                ("rechnung_kunde_id", "rechnung_nr"),
                ref("adresse"),
                ("kunde_id", "nr"),
            ),
        ],
    )
    artikel = Table("artikel", [col("id"), col("bezeichnung", "varchar(200)")], [pk("id")])
    # schwache Entität mit zusammengesetztem PK
    bestellposition = Table(
        "bestellposition",
        [col("bestellung_id"), col("pos", "smallint"), col("artikel_id"), col("menge")],
        [pk("bestellung_id", "pos")],
        [
            ForeignKey("fk_pos_bestellung", ("bestellung_id",), ref("bestellung"), ("id",)),
            ForeignKey("fk_pos_artikel", ("artikel_id",), ref("artikel"), ("id",)),
        ],
    )
    kategorie = Table("kategorie", [col("id"), col("name", "varchar(80)")], [pk("id")])
    # n:m-Zwischentabelle
    artikel_kategorie = Table(
        "artikel_kategorie",
        [col("artikel_id"), col("kategorie_id")],
        [pk("artikel_id", "kategorie_id"), Index("ix_kategorie", IndexKind.INDEX, ("kategorie_id",))],
        [
            ForeignKey("fk_ak_artikel", ("artikel_id",), ref("artikel"), ("id",)),
            ForeignKey("fk_ak_kategorie", ("kategorie_id",), ref("kategorie"), ("id",)),
        ],
    )
    # Selbstreferenz
    mitarbeiter = Table(
        "mitarbeiter",
        [col("id"), col("vorgesetzter_id", nullable=True)],
        [pk("id")],
        [ForeignKey("fk_vorgesetzter", ("vorgesetzter_id",), ref("mitarbeiter"), ("id",))],
    )

    return Diagram(
        [
            Database(
                shop_db,
                Dialect.MYSQL,
                [
                    Schema(
                        None,
                        [
                            kunde,
                            kundenprofil,
                            adresse,
                            bestellung,
                            artikel,
                            bestellposition,
                            kategorie,
                            artikel_kategorie,
                            mitarbeiter,
                        ],
                    )
                ],
                charset="utf8mb4",
            ),
            Database(master_db, Dialect.MYSQL, [Schema(None, [land])]),
        ]
    )


@pytest.fixture
def test_schema() -> Diagram:
    return build_test_schema()
