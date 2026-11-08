-- Testschema für den ER-Diagramm-Editor (SQL Server 2017+)
-- Entspricht build_test_schema() in tests/conftest.py und PROJEKT.md, Abschnitt 11.
-- Wie bei PostgreSQL: zwei Schemas `shop` und `stammdaten` in einer Datenbank.
--
-- Einspielen mit Windows-Anmeldung:
--   sqlcmd -S localhost -E -C -i tests/schema/mssql.sql
-- oder in SQL Server Management Studio öffnen und ausführen (F5).
--
-- ACHTUNG: Löscht und erzeugt die Datenbank erd_test_shop neu.

USE master;
GO
IF DB_ID('erd_test_shop') IS NOT NULL
BEGIN
    ALTER DATABASE erd_test_shop SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
    DROP DATABASE erd_test_shop;
END
GO
CREATE DATABASE erd_test_shop;
GO
USE erd_test_shop;
GO
CREATE SCHEMA shop;
GO
CREATE SCHEMA stammdaten;
GO

CREATE TABLE stammdaten.land (
    code char(2)      NOT NULL,
    name nvarchar(80) NOT NULL,
    CONSTRAINT land_pkey PRIMARY KEY (code)
);

-- NULL-fähiger FK über Schemagrenze, IDENTITY, Default, Kommentare
CREATE TABLE shop.kunde (
    id        int           IDENTITY(1,1) NOT NULL,
    name      nvarchar(100) NOT NULL CONSTRAINT df_kunde_name DEFAULT '',
    land_code char(2)       NULL,
    CONSTRAINT kunde_pkey PRIMARY KEY (id),
    CONSTRAINT fk_kunde_land FOREIGN KEY (land_code) REFERENCES stammdaten.land (code)
);
CREATE INDEX ft_name ON shop.kunde (name) INCLUDE (land_code);  -- INCLUDE-Spalte ist kein Schlüssel

-- 1:1 über UNIQUE-FK
CREATE TABLE shop.kundenprofil (
    id       int           NOT NULL,
    kunde_id int           NOT NULL,
    bio      nvarchar(max) NULL,
    CONSTRAINT kundenprofil_pkey PRIMARY KEY (id),
    CONSTRAINT uq_kunde UNIQUE (kunde_id),
    CONSTRAINT fk_profil_kunde FOREIGN KEY (kunde_id) REFERENCES shop.kunde (id)
);

-- schwache Entität, Ziel eines mehrspaltigen FK
CREATE TABLE shop.adresse (
    kunde_id int          NOT NULL,
    nr       smallint     NOT NULL,
    ort      nvarchar(80) NOT NULL,
    CONSTRAINT adresse_pkey PRIMARY KEY (kunde_id, nr),
    CONSTRAINT fk_adresse_kunde FOREIGN KEY (kunde_id) REFERENCES shop.kunde (id)
);

-- NOT-NULL-FK, zwei mehrspaltige FKs auf dieselbe Tabelle
CREATE TABLE shop.bestellung (
    id                int      NOT NULL,
    kunde_id          int      NOT NULL,
    liefer_kunde_id   int      NULL,
    liefer_nr         smallint NULL,
    rechnung_kunde_id int      NULL,
    rechnung_nr       smallint NULL,
    CONSTRAINT bestellung_pkey PRIMARY KEY (id),
    CONSTRAINT fk_bestellung_kunde FOREIGN KEY (kunde_id) REFERENCES shop.kunde (id),
    CONSTRAINT fk_lieferadresse FOREIGN KEY (liefer_kunde_id, liefer_nr)
        REFERENCES shop.adresse (kunde_id, nr),
    CONSTRAINT fk_rechnungsadresse FOREIGN KEY (rechnung_kunde_id, rechnung_nr)
        REFERENCES shop.adresse (kunde_id, nr)
);
CREATE INDEX ix_kunde ON shop.bestellung (kunde_id);

CREATE TABLE shop.artikel (
    id          int            IDENTITY(1,1) NOT NULL,
    bezeichnung nvarchar(200)  NOT NULL,
    preis       decimal(10,2)  NOT NULL CONSTRAINT df_artikel_preis DEFAULT ((0)),
    angelegt    datetime2(3)   NOT NULL CONSTRAINT df_artikel_angelegt DEFAULT (sysutcdatetime()),
    CONSTRAINT artikel_pkey PRIMARY KEY (id)
);

-- schwache Entität mit zusammengesetztem PK
CREATE TABLE shop.bestellposition (
    bestellung_id int      NOT NULL,
    pos           smallint NOT NULL,
    artikel_id    int      NOT NULL,
    menge         int      NOT NULL,
    CONSTRAINT bestellposition_pkey PRIMARY KEY (bestellung_id, pos),
    CONSTRAINT fk_pos_bestellung FOREIGN KEY (bestellung_id) REFERENCES shop.bestellung (id)
        ON DELETE CASCADE,
    CONSTRAINT fk_pos_artikel FOREIGN KEY (artikel_id) REFERENCES shop.artikel (id)
);

CREATE TABLE shop.kategorie (
    id   int          NOT NULL,
    name nvarchar(80) NOT NULL,
    CONSTRAINT kategorie_pkey PRIMARY KEY (id)
);

-- n:m-Zwischentabelle
CREATE TABLE shop.artikel_kategorie (
    artikel_id   int NOT NULL,
    kategorie_id int NOT NULL,
    CONSTRAINT artikel_kategorie_pkey PRIMARY KEY (artikel_id, kategorie_id),
    CONSTRAINT fk_ak_artikel   FOREIGN KEY (artikel_id)   REFERENCES shop.artikel (id),
    CONSTRAINT fk_ak_kategorie FOREIGN KEY (kategorie_id) REFERENCES shop.kategorie (id)
);
CREATE INDEX ix_kategorie ON shop.artikel_kategorie (kategorie_id);

-- Selbstreferenz
CREATE TABLE shop.mitarbeiter (
    id              int NOT NULL,
    vorgesetzter_id int NULL,
    CONSTRAINT mitarbeiter_pkey PRIMARY KEY (id),
    CONSTRAINT fk_vorgesetzter FOREIGN KEY (vorgesetzter_id) REFERENCES shop.mitarbeiter (id)
);
GO

-- Kommentare (Extended Properties)
EXEC sp_addextendedproperty 'MS_Description', N'Kundenstamm',
     'SCHEMA', 'shop', 'TABLE', 'kunde';
EXEC sp_addextendedproperty 'MS_Description', N'Anzeigename',
     'SCHEMA', 'shop', 'TABLE', 'kunde', 'COLUMN', 'name';
EXEC sp_addextendedproperty 'MS_Description', N'ISO-Ländercodes',
     'SCHEMA', 'stammdaten', 'TABLE', 'land';
GO
