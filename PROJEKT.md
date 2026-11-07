# ER-Diagramm-Editor – Projektbeschreibung

> Arbeitstitel. Ersatz für den EER-Diagramm-Editor, der in der MySQL Workbench nicht mehr enthalten ist.

## 1. Ziel

Eine Windows-Desktop-Anwendung, die Datenbankschemas als ER-Diagramm darstellt:
Entitäten mit Attributen, Primär- und Fremdschlüsseln sowie Indexen, die Beziehungen
zwischen den Entitäten und die Datenbanken als umschließende Behälter. Das Diagramm
muss sich drucken und als PDF exportieren lassen.

Schwerpunkt der ersten Version ist das **Darstellen** bestehender Schemas.
Bearbeiten und Forward Engineering folgen in späteren Ausbaustufen.

Unterstützte Dialekte: **MySQL**, **PostgreSQL**, **Microsoft SQL Server**.

## 2. Technik

| Bereich | Wahl |
|---|---|
| Sprache | Python 3.12+ |
| Oberfläche | PySide6 (Qt 6.7+), `QGraphicsScene` / `QGraphicsView` |
| Stil | Qt-Stil `windows11` oder Fusion mit eigener Hell/Dunkel-Palette |
| MySQL | PyMySQL oder mysql-connector-python |
| PostgreSQL | psycopg |
| SQL Server | pyodbc |
| Passwörter | `keyring` (Windows-Anmeldeinformationsverwaltung) |
| Auto-Layout | networkx (Raster oder kraftbasiert) |
| Icons | SVG über `QSvgRenderer` (Vorlagen: Tabler / Lucide, MIT/ISC) |
| Drucken/PDF | `QPrinter`, `QPdfWriter`, Druckvorschau |
| Auslieferung | PyInstaller (oder Nuitka) als Windows-`.exe` |
| Entwicklung | VS Code mit Coding-Agent |

Keine Docker-Abhängigkeit. Das Programm ist eine normale, per Doppelklick startbare Anwendung.

## 3. Architektur

Strikte Trennung in drei Schichten:

1. **Dialekt-Adapter** lesen das Schema aus der jeweiligen Datenbank.
2. **Neutrales Modell** beschreibt das Schema unabhängig vom Dialekt.
3. **Darstellung** kennt nur das neutrale Modell, nie den Dialekt.

Das Zeichnen der Linienenden (Notation) ist ein austauschbares Modul.

Vorschlag für die Ordnerstruktur:

```
erd_editor/
  main.py
  model/          # neutrales Datenmodell (dataclasses)
  adapters/       # base.py, mysql.py, postgres.py, mssql.py
  analysis/       # Kardinalitäten, identifizierende Beziehungen
  diagram/        # Scene, EntityItem, ContainerItem, RelationItem
  notation/       # base.py, crowsfoot.py (später uml.py, minmax.py, idef1x.py)
  layout/         # Auto-Layout
  persistence/    # Laden/Speichern des Diagramms (JSON)
  export/         # Drucken, PDF (später PNG, SVG)
  resources/      # SVG-Icons
tests/
  schema/         # gemeinsames Testschema für alle drei Dialekte
```

## 4. Neutrales Datenmodell

- **Diagramm** – enthält eine oder mehrere Datenbanken
- **Datenbank** – Name, Dialekt, Verbindungsprofil, Server/Version, Zeichensatz/Kollation, Farbe
- **Schema** – nur bei PostgreSQL und SQL Server (`public`, `dbo`, …); bei MySQL ist Datenbank = Schema
- **Tabelle** – Name, Kommentar, Spalten, Indexe
- **Spalte** – Name, nativer Datentyp, NULL-fähig, Default, Kommentar, ist PK, ist FK, ist indiziert
- **Index** – Name, Typ (PRIMARY, UNIQUE, INDEX, FULLTEXT, …), Spalten in Reihenfolge
- **Beziehung** – FK-Name, Quelltabelle und -spalten, Zieltabelle und -spalten,
  identifizierend ja/nein, Kardinalität, optionale manuelle Überschreibungen

Datentypen werden zur Anzeige **nativ** übernommen, so wie die Datenbank sie liefert.
Ein Typ-Mapping zwischen Dialekten wird erst für Forward Engineering benötigt.

## 5. Schema einlesen (Reverse Engineering, lesend)

| Information | MySQL | PostgreSQL | SQL Server |
|---|---|---|---|
| Tabellen, Spalten | `information_schema` | `information_schema` | `information_schema` / `sys` |
| PK, FK | `TABLE_CONSTRAINTS`, `KEY_COLUMN_USAGE`, `REFERENTIAL_CONSTRAINTS` | `information_schema` bzw. `pg_constraint` | `sys.foreign_keys`, `sys.foreign_key_columns` |
| Indexe | `information_schema.STATISTICS` | `pg_catalog` (`pg_index`, `pg_class`) | `sys.indexes`, `sys.index_columns` |
| Kommentare | `COLUMN_COMMENT`, `TABLE_COMMENT` | `pg_description` | `sys.extended_properties` |

Reihenfolge der Umsetzung: **MySQL → PostgreSQL → SQL Server.**
Der zweite Adapter prüft, ob das neutrale Modell trägt.

## 6. Darstellung

### Entität (Box)
- Kopfzeile mit Tabellenname
- Attributliste: Icon, Spaltenname, Datentyp
- Abgesetzter Indexbereich unten: Indexname, Typ, Spalten
- Abgerundete Ecken, flache Farben, gut lesbare Schrift

### Icons
- **PK:** goldener Schlüssel
- **FK:** Schlüssel in zweiter Farbe (blau oder grau)
- **PK + FK:** kombiniertes Symbol (z. B. goldener Schlüssel mit blauem Punkt)
- Optional übrige Spalten: Raute gefüllt = NOT NULL, Raute leer = NULL-fähig
- Indizierte Spalten mit kleinem Zusatzsymbol markieren

### Datenbank als Behälter
- Rechteck hinter den Entitäten, Kopfzeile mit Datenbankname, Server/Version, Zeichensatz
- **Mehrere Datenbanken in einem Diagramm**, jede in eigener Farbe
- Druckfreundlich: helle Pastellfüllung, kräftigerer Rand, Beschriftung; auch in Schwarz-Weiß unterscheidbar
- Rahmen verschieben → Entitäten wandern mit; Entität an den Rand ziehen → Rahmen wächst
- Jede Entität gehört genau zu einer Datenbank
- Schemas (PostgreSQL, SQL Server) als hellere innere Rechtecke
- Jeder Behälter hat sein eigenes Verbindungsprofil; Datenbanken verschiedener Server
  und Dialekte können nebeneinander stehen

### Beziehungen
- Linie von der FK-Spalte zur referenzierten Tabelle
- **Identifizierend** (FK-Spalten vollständig im PK der Kindtabelle, schwache Entität): durchgezogene Linie
- **Nicht-identifizierend:** gestrichelte Linie
- Datenbankübergreifende FKs (nur MySQL, gleicher Server) laufen über Rahmengrenzen

### Notation
- Version 1: **Krähenfuß (IE)**
- Später: UML (`1`, `0..*`), (min,max), IDEF1X – nur Linienenden und Beschriftung tauschen
- Chen-Notation als eigene konzeptionelle Ansicht (Rauten, Ovale) – spätere Ausbaustufe

### Kardinalitäten (automatisch abgeleitet)
- FK-Spalte NULL-fähig → optional (0..1), sonst verpflichtend (1..1)
- UNIQUE-Index auf den FK-Spalten oder FK = PK → 1:1, sonst 1:n
- „Elternteil hat mindestens ein Kind“ ist nicht aus dem Schema ableitbar → später manueller Schalter

### Sonderfälle, die sauber gezeichnet werden müssen
- Selbstreferenz (z. B. `mitarbeiter.vorgesetzter_id`)
- Mehrspaltige Fremdschlüssel
- Mehrere FKs zwischen denselben zwei Tabellen
- Zwischentabellen für n:m (zwei identifizierende Beziehungen)

## 7. Interaktion

- Verbindung anlegen, Dialekt wählen, Datenbank/Schema auswählen
- Schema einlesen, Diagramm automatisch anordnen
- Boxen und Rahmen verschieben, zoomen, scrollen
- **Neu einlesen ohne Layoutverlust:** neue Tabellen kommen hinzu, bestehende Positionen bleiben

## 8. Speicherung

- Diagrammdatei als JSON, **Schema und Layout getrennt** (Positionen, Farben, Überschreibungen)
- Verbindungsprofile ohne Passwort im JSON; Passwörter über `keyring`

## 9. Drucken und Export

- Druck mit Vorschau, Seitenformat (A4, A3, Hoch-/Querformat)
- PDF-Export
- Später: PNG und SVG (für Artikel und Bücher)

## 10. Umfang

### Version 1
- [x] Neutrales Datenmodell
- [ ] MySQL-Adapter (Tabellen, Spalten, PK, FK, Indexe, Kommentare)
- [ ] Entitätsboxen mit Icons und Indexbereich
- [ ] Datenbank-Behälter, mehrere Datenbanken mit eigenen Farben
- [ ] Beziehungen: Krähenfuß, identifizierend/nicht-identifizierend, abgeleitete Kardinalitäten
- [ ] Sonderfälle (Selbstreferenz, mehrspaltige FKs, Mehrfach-FKs)
- [ ] Auto-Layout
- [ ] Verschieben, Zoom
- [ ] Speichern/Laden (JSON), Neu einlesen ohne Layoutverlust
- [ ] Passwörter über `keyring`
- [ ] Drucken und PDF-Export

### Version 2
- [ ] PostgreSQL- und SQL-Server-Adapter, Schemas als innere Rechtecke
- [ ] Darstellungsoptionen: Datentypen ein/aus, nur Schlüsselspalten, Kommentare
- [ ] Mehrere Teildiagramme pro Datenbank (z. B. „Bestellwesen“, „Personal“)
- [ ] Export PNG/SVG
- [ ] Undo/Redo (`QUndoStack`)
- [ ] Manuelle Überschreibungen (schwache Entität, Mindestkardinalität)
- [ ] Weitere Notationen (UML, (min,max), IDEF1X)

### Später
- [ ] Views als eigene Objektart
- [ ] Bearbeiten der Struktur im Editor
- [ ] Forward Engineering mit Typ-Mapping zwischen Dialekten
- [ ] n:m-Zwischentabellen optional als eine Linie
- [ ] Chen-Ansicht
- [ ] Optional: Modell über einen MCP-Server bereitstellen

## 11. Testen

Ein gemeinsames **Testschema**, das in MySQL, PostgreSQL und SQL Server eingespielt wird
(lokal installierte Datenbanken, kein Docker nötig). Es enthält alle Sonderfälle:

- schwache Entität mit zusammengesetztem PK (z. B. `bestellposition`)
- n:m-Zwischentabelle (z. B. `artikel_kategorie`)
- Selbstreferenz (`mitarbeiter.vorgesetzter_id`)
- mehrspaltiger FK
- zwei FKs zwischen denselben Tabellen
- 1:1-Beziehung über UNIQUE-FK
- NULL-fähiger und NOT-NULL-FK
- verschiedene Indextypen inkl. FULLTEXT (MySQL)
- Spalten- und Tabellenkommentare
- bei MySQL zusätzlich ein FK in eine zweite Datenbank

## 12. Offene Punkte

- Endgültiger Projektname
- Sprache der Oberfläche (Deutsch, später mehrsprachig?) – Code-Bezeichner: Englisch, Kommentare: Deutsch (entschieden)
- Lizenz für die Veröffentlichung auf GitHub
