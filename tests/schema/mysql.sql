-- Testschema für den ER-Diagramm-Editor (MySQL 8)
-- Entspricht build_test_schema() in tests/conftest.py und PROJEKT.md, Abschnitt 11.
--
-- Einspielen:
--   mysql -u root -p < tests/schema/mysql.sql
--
-- ACHTUNG: Löscht und erzeugt die Datenbanken erd_test_shop und erd_test_stammdaten neu.

DROP DATABASE IF EXISTS erd_test_shop;
DROP DATABASE IF EXISTS erd_test_stammdaten;

CREATE DATABASE erd_test_stammdaten CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE erd_test_shop       CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
USE erd_test_stammdaten;

CREATE TABLE land (
    code CHAR(2)     NOT NULL,
    name VARCHAR(80) NOT NULL,
    PRIMARY KEY (code)
) ENGINE = InnoDB COMMENT = 'ISO-Ländercodes';

-- ---------------------------------------------------------------------------
USE erd_test_shop;

-- NULL-fähiger FK in eine zweite Datenbank, FULLTEXT-Index, Kommentare
CREATE TABLE kunde (
    id        INT          NOT NULL AUTO_INCREMENT,
    name      VARCHAR(100) NOT NULL DEFAULT '' COMMENT 'Anzeigename',
    land_code CHAR(2)      NULL,
    PRIMARY KEY (id),
    FULLTEXT INDEX ft_name (name),
    CONSTRAINT fk_kunde_land FOREIGN KEY (land_code)
        REFERENCES erd_test_stammdaten.land (code)
) ENGINE = InnoDB COMMENT = 'Kundenstamm';

-- 1:1 über UNIQUE-FK
CREATE TABLE kundenprofil (
    id       INT  NOT NULL,
    kunde_id INT  NOT NULL,
    bio      TEXT NULL,
    PRIMARY KEY (id),
    UNIQUE INDEX uq_kunde (kunde_id),
    CONSTRAINT fk_profil_kunde FOREIGN KEY (kunde_id) REFERENCES kunde (id)
) ENGINE = InnoDB;

-- schwache Entität, Ziel eines mehrspaltigen FK
CREATE TABLE adresse (
    kunde_id INT         NOT NULL,
    nr       SMALLINT    NOT NULL,
    ort      VARCHAR(80) NOT NULL,
    PRIMARY KEY (kunde_id, nr),
    CONSTRAINT fk_adresse_kunde FOREIGN KEY (kunde_id) REFERENCES kunde (id)
) ENGINE = InnoDB;

-- NOT-NULL-FK, zwei mehrspaltige FKs auf dieselbe Tabelle
CREATE TABLE bestellung (
    id                INT      NOT NULL,
    kunde_id          INT      NOT NULL,
    liefer_kunde_id   INT      NULL,
    liefer_nr         SMALLINT NULL,
    rechnung_kunde_id INT      NULL,
    rechnung_nr       SMALLINT NULL,
    PRIMARY KEY (id),
    INDEX ix_kunde (kunde_id),
    CONSTRAINT fk_bestellung_kunde FOREIGN KEY (kunde_id) REFERENCES kunde (id),
    CONSTRAINT fk_lieferadresse FOREIGN KEY (liefer_kunde_id, liefer_nr)
        REFERENCES adresse (kunde_id, nr),
    CONSTRAINT fk_rechnungsadresse FOREIGN KEY (rechnung_kunde_id, rechnung_nr)
        REFERENCES adresse (kunde_id, nr)
) ENGINE = InnoDB;

CREATE TABLE artikel (
    id          INT          NOT NULL,
    bezeichnung VARCHAR(200) NOT NULL,
    PRIMARY KEY (id)
) ENGINE = InnoDB;

-- schwache Entität mit zusammengesetztem PK
CREATE TABLE bestellposition (
    bestellung_id INT      NOT NULL,
    pos           SMALLINT NOT NULL,
    artikel_id    INT      NOT NULL,
    menge         INT      NOT NULL,
    PRIMARY KEY (bestellung_id, pos),
    CONSTRAINT fk_pos_bestellung FOREIGN KEY (bestellung_id) REFERENCES bestellung (id)
        ON DELETE CASCADE,
    CONSTRAINT fk_pos_artikel FOREIGN KEY (artikel_id) REFERENCES artikel (id)
) ENGINE = InnoDB;

CREATE TABLE kategorie (
    id   INT         NOT NULL,
    name VARCHAR(80) NOT NULL,
    PRIMARY KEY (id)
) ENGINE = InnoDB;

-- n:m-Zwischentabelle
CREATE TABLE artikel_kategorie (
    artikel_id   INT NOT NULL,
    kategorie_id INT NOT NULL,
    PRIMARY KEY (artikel_id, kategorie_id),
    INDEX ix_kategorie (kategorie_id),
    CONSTRAINT fk_ak_artikel   FOREIGN KEY (artikel_id)   REFERENCES artikel (id),
    CONSTRAINT fk_ak_kategorie FOREIGN KEY (kategorie_id) REFERENCES kategorie (id)
) ENGINE = InnoDB;

-- Selbstreferenz
CREATE TABLE mitarbeiter (
    id              INT NOT NULL,
    vorgesetzter_id INT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_vorgesetzter FOREIGN KEY (vorgesetzter_id) REFERENCES mitarbeiter (id)
) ENGINE = InnoDB;

-- Views: über die Datenbankgrenze, mit Aggregat und eine View auf Views
CREATE VIEW v_kunde_land AS
    SELECT k.id, k.name, l.name AS land
    FROM kunde k LEFT JOIN erd_test_stammdaten.land l ON l.code = k.land_code;

CREATE VIEW v_bestellsumme AS
    SELECT b.id AS bestellung_id, b.kunde_id, COUNT(p.pos) AS positionen
    FROM bestellung b LEFT JOIN bestellposition p ON p.bestellung_id = b.id
    GROUP BY b.id, b.kunde_id;

CREATE VIEW v_kunde_bestellungen AS
    SELECT v.id, v.name, s.positionen
    FROM v_kunde_land v JOIN v_bestellsumme s ON s.kunde_id = v.id;
