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
| Icons | Eigene SVGs über `QSvgRenderer` (Diagramm: `resources/icons`, Symbolleiste: `resources/toolbar`, erzeugt mit `tools/make_toolbar_icons.py`) |
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
- Später: (min,max), IDEF1X – nur Linienenden und Beschriftung tauschen (UML bewusst nicht)
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
- „Ganzes Diagramm“ (Strg+0) folgt der Fenstergröße (z. B. Vollbild), bis man selbst
  zoomt; kleine Diagramme höchstens 100 %
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
- [x] MySQL-Adapter (Tabellen, Spalten, PK, FK, Indexe, Kommentare)
- [x] Entitätsboxen mit Icons und Indexbereich
- [x] Datenbank-Behälter, mehrere Datenbanken mit eigenen Farben
- [x] Beziehungen: Krähenfuß, identifizierend/nicht-identifizierend, abgeleitete Kardinalitäten
- [x] Sonderfälle (Selbstreferenz, mehrspaltige FKs, Mehrfach-FKs)
- [x] Auto-Layout
- [x] Verschieben, Zoom
- [x] Darstellungsgröße 100–200 % (Schrift, Boxen, Symbole; gespeichert)
- [x] Datenbank-Rahmen von Hand aufziehen
- [x] Speichern/Laden (JSON), Neu einlesen ohne Layoutverlust
- [x] Passwörter über `keyring` (Verbindungsdialog, Profile ohne Passwort in der Datei)
- [x] Drucken, PDF- und PNG-Export

### Version 2
- [x] PostgreSQL-Adapter
- [x] Schemas als innere Rechtecke (PostgreSQL, SQL Server)
- [x] SQL-Server-Adapter (Windows-Anmeldung oder SQL-Login)
- [x] Darstellungsoptionen: Datentypen ein/aus, nur Schlüsselspalten, Kommentare (dazu: keine Spalten, Indexbereich)
- [x] Mehrere Teildiagramme (z. B. „Bestellwesen“, „Personal“) als Reiter mit eigenem Layout
- [x] Export SVG (PNG ist in Version 1 vorgezogen)
- [x] Undo/Redo (`QUndoStack`) – Layout-Aktionen, Neu einlesen, Datenbanken hinzufügen; Fundament für Version 3
- [x] Manuelle Überschreibungen (schwache Entität, Mindestkardinalität) per Rechtsklick auf die Linie
- [x] Weitere Notationen ((min,max), IDEF1X) – UML bewusst nicht

### Version 3 – Modellieren
Entitäten und Beziehungen selbst anlegen, ohne Datenbankverbindung.

- [x] Datei → Neu; „Neue Datenbank (ohne Verbindung)“ mit Dialektwahl
- [x] Modellierte und eingelesene Datenbanken unterscheiden: eingelesene bleiben
      schreibgeschützt; „Als Modell übernehmen“ macht die Datenbank selbst bearbeitbar
      (Verbindung wird gelöst, „Neu einlesen“ lässt sie danach unverändert);
      Bearbeitungsversuch an eingelesenen Datenbanken → Hinweisfenster „Schreibgeschützt“
- [x] Entität anlegen/bearbeiten (Doppelklick): Name, Kommentar, Spalten
      (Name, Typ, NULL, Default, PK, Auto-Increment)
- [x] Indexe im Tabelleneditor bearbeiten (UNIQUE, INDEX, FULLTEXT …): Reiter „Indexe“,
      Spalten anhaken und ordnen; Umbenennen/Verschieben/Löschen von Spalten wird nachgezogen
- [x] Datentyp-Auswahl je Dialekt (nativ, ohne Mapping)
- [x] Beziehungs-Werkzeug (Strg+B): vom Kind zum Elternteil ziehen, 1:n/1:1 und
      identifizierend/nicht-identifizierend wählen; FK-Spalten werden automatisch
      angelegt (Typ vom PK, bei identifizierend in den PK, bei 1:1 UNIQUE)
- [x] Löschen und Umbenennen mit allen Folgen (abhängige FKs, Positionen, Teildiagramme)
- [x] Beziehung löschen (Rechtsklick auf die Linie), mit oder ohne FK-Spalten
- [x] Alle Bearbeitungen über Undo/Redo
- [x] Szene gezielt aktualisieren statt komplett neu aufbauen
      (Bearbeiten, Rückgängig, Neu einlesen: nur geänderte Boxen und Linien;
      Auswahl und Bildausschnitt bleiben)
- [x] SQL-Export (`CREATE TABLE …`) für MySQL, PostgreSQL und SQL Server
      (Roundtrip-Test gegen alle drei Server: Skript ausführen, wieder einlesen, gleiches Schema)

### Später
- [x] Views als eigene Objektart: einlesen (alle Dialekte, MariaDB über den SQL-Text),
      abgesetzte Box (gestrichelt, kursiv, „VIEW“), gepunktete Abhängigkeitslinien,
      modellieren (Modell → Neue View …, Abhängigkeiten aus dem SQL erkannt),
      SQL-Export in Abhängigkeitsreihenfolge; materialisierte Views noch nicht
- [x] Notizen (Infoboxen): frei oder mit einer Tabelle verbunden, verschieben, Größe ändern,
      Doppelklick bearbeitet, Kontextmenüs (Tabelle, Notiz, freie Fläche), Entf löscht;
      gehören zum Layout der jeweiligen Ansicht (je Teildiagramm eigene Notizen)
- [x] Symbolleiste: Datei, Rückgängig, Modellieren (Datenbank, Tabelle, View, Beziehung,
      Notiz), Zoom/Anordnen, PDF/SQL; eigene SVG-Icons, Tooltips mit Tastenkürzel,
      ausgegraut, wenn nicht möglich; Rechtsklick: nur Symbole / mit Text (gespeichert)
- [ ] Modell mit Datenbank abgleichen (Unterschiede als `ALTER TABLE`)
- [ ] Forward Engineering mit Typ-Mapping zwischen Dialekten
- [ ] Mehrseitiger Druck (Poster) für große Diagramme
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

## 12. Hinweise für die Entwicklung

- Umgebung: `.venv` (Python 3.11), Projekt editierbar installiert (`pip install -e .`)
- Tests: `.venv\Scripts\python -m pytest -q` (Qt läuft dabei ohne Fenster)
- Start: `.venv\Scripts\python -m erd_editor.main` (ohne Argumente öffnet „Datenbank einlesen“)
- Testschema `erd_test_shop` ist eingespielt in MySQL 8.4 (Port 3306) und MariaDB 13 (Port 3307),
  jeweils mit `erd_test_stammdaten`, sowie in PostgreSQL und SQL Server (`tests/schema/*.sql`)
- Live-Tests holen Passwörter aus dem keyring, Dienst `erd-editor-test`:
  Konten `root` (MySQL), `root@mariadb` (MariaDB) und `postgres` (PostgreSQL);
  SQL Server per Windows-Anmeldung
- MariaDB läuft über den MySQL-Adapter; er gleicht die Abweichungen an MySQL 8 an
  (Anzeigebreite `int(11)` → `int`, gequotete Defaults `'abc'`/`NULL` → `abc`/None)
- Die Anwendung selbst speichert Passwörter unter dem Dienst `erd-editor`
- Tests, die ein echtes Menü oder einen Dialog öffnen würden, nicht mit `exec()` schreiben,
  sondern das Menü bauen und Aktionen direkt auslösen (sonst hängt der Testlauf)
- Modale Dialoge nicht direkt aus einem Maus-Handler eines Szenen-Items öffnen, sondern
  per `QTimer.singleShot(0, …)` danach – sonst bleibt das Item Mouse-Grabber und fängt
  alle weiteren Klicks ab (siehe `EntityItem.mouseDoubleClickEvent`)
- `QTest.mouseDClick` schickt nur das DblClick-Ereignis ohne vorheriges Drücken/Loslassen;
  für realistische Doppelklicks `mousePress`, `mouseRelease`, `mouseDClick`, `mouseRelease`
- Views stehen als `Table` mit `kind=VIEW` in derselben Liste wie Tabellen; `definition`
  enthält nur den SELECT-Teil (MySQL: ohne eigenen Datenbanknamen), `view_dependencies`
  die benutzten Objekte. `_tables()` im SQL-Export liefert nur echte Tabellen
- Notizen liegen in `Layout.notes` (`Note`: id, text, frame, entity) und laufen damit über
  `record_layout()`/Rückgängig, Speichern und Teildiagramme; Szene: `apply_notes()`,
  Bedienung im `NotesMixin` (`ui/notes.py`). Verschieben einer Tabelle von Hand nimmt
  ihre Notiz nicht mit, „Automatisch anordnen“ schon
- Testschema enthält drei Views (`v_kunde_land`, `v_bestellsumme`, `v_kunde_bestellungen`)
- - Modelländerungen, Rückgängig und Neu einlesen laufen über `show_view(..., keep_view=True)`
  → `DiagramScene.update_diagram()`; nur bei geänderten Datenbanken/Schemas/Farben/
  Darstellungsgröße wird über `load()` komplett neu aufgebaut
- Der Tabelleneditor liefert Indexe mit den **neuen** Spaltennamen
  (`replace_table(..., final_indexes=True)`), Fremdschlüssel mit den alten

- Symbolleiste (`ui/toolbar.py`): nutzt dieselben `QAction`s wie die Menüs
  (`named_actions`, beim Menübau über `_named()` gemerkt); Zustände in `_sync_toolbar()`,
  aufgerufen aus `_sync_menus()`. Icons in `resources/toolbar/*.svg`, erzeugt mit
  `tools/make_toolbar_icons.py` (Farben/Formen dort ändern, dann neu erzeugen)
- Einstellungen (z. B. Darstellung der Symbolleiste) über `QSettings` in der Registry
  (`HKCU\Software\ErdEditor`); Tests leiten sie per `conftest._settings_in_tmp` in eine
  temporäre INI-Datei um

### Anwendungssymbol und späterer Build

- Symbol: `erd_editor/resources/app.ico` (7 Größen ab 16×16), gesetzt per `app.setWindowIcon()` in `main.py`
- Windows: eigene AppUserModelID (`ErdEditor.ErDiagrammEditor.App`) über
  `SetCurrentProcessExplicitAppUserModelID`, vor dem ersten Fenster – sonst zeigt die
  Taskleiste beim Start mit `python` das Python-Symbol
- PyInstaller-Build (noch nicht eingerichtet): `--icon` setzt das Symbol der `.exe`
  (Explorer), `--add-data` liefert die Ressourcen für Fenster und Diagramm mit:
  ```
  pyinstaller --windowed --name "ER-Diagramm-Editor" ^
      --icon erd_editor/resources/app.ico ^
      --add-data "erd_editor/resources;erd_editor/resources" ^
      erd_editor/main.py
  ```

## 13. Offene Punkte

- Endgültiger Projektname
- Sprache der Oberfläche (Deutsch, später mehrsprachig?) – Code-Bezeichner: Englisch, Kommentare: Deutsch (entschieden)
- Lizenz: MIT (entschieden, `LICENSE`)
- Repository: privat auf GitHub, `havefuntocode/try-to-make-an-erd-modeler-with-agentic-coding`
  (Remote `origin`, Branch `main`); Commits nur mit der Repo-eigenen Git-Identität
