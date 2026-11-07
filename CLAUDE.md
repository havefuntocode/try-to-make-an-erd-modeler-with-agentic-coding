# ER-Diagramm-Editor

## Sprache
Bitte antworte immer auf **Deutsch**. Kommentare und Docstrings im Code auf Deutsch,
Bezeichner (Klassen, Funktionen, Variablen) auf Englisch.

## Projekt
Windows-Desktop-Anwendung, die Datenbankschemas (MySQL, PostgreSQL, SQL Server)
als ER-Diagramm darstellt – Ersatz für den fehlenden EER-Editor der MySQL Workbench.

**Die vollständige Projektbeschreibung steht in `PROJEKT.md`. Lies sie vor jeder größeren Änderung.**

## Technik
- Python 3.12+, PySide6 (Qt 6.7+), `QGraphicsScene` / `QGraphicsView`
- MySQL: PyMySQL · PostgreSQL: psycopg · SQL Server: pyodbc
- Passwörter nur über `keyring`, niemals im Klartext oder in JSON-Dateien
- Auslieferung später als `.exe` mit PyInstaller

## Architekturregeln
- Drei Schichten: `adapters/` (Dialekt) → `model/` (neutral) → `diagram/` (Darstellung)
- `diagram/` und `notation/` importieren **nie** aus `adapters/`
- Das neutrale Modell besteht aus `dataclasses` ohne Qt-Abhängigkeit
- Notationen (Linienenden) sind austauschbar über eine gemeinsame Basisklasse in `notation/base.py`
- Datentypen werden nativ angezeigt, kein Typ-Mapping in Version 1
- Schema und Layout werden im JSON getrennt gespeichert

## Ordnerstruktur
```
erd_editor/
  main.py         # Einstiegspunkt
  model/          # neutrales Datenmodell
  adapters/       # base.py, mysql.py, postgres.py, mssql.py
  analysis/       # Kardinalitäten, identifizierende Beziehungen
  diagram/        # Scene, EntityItem, ContainerItem, RelationItem
  notation/       # base.py, crowsfoot.py
  layout/         # Auto-Layout
  persistence/    # Laden/Speichern (JSON)
  export/         # Drucken, PDF
  resources/icons # SVG-Icons
tests/
  schema/         # Testschema für alle drei Dialekte
```

## Arbeitsweise
- Version 1 aus `PROJEKT.md` Abschnitt 10 Schritt für Schritt umsetzen, mit MySQL beginnen
- Erledigte Punkte in der Checkliste in `PROJEKT.md` abhaken
- Kleine, nachvollziehbare Schritte; vor größeren Umbauten nachfragen
- Tests mit pytest; Logik in `model/` und `analysis/` ohne Datenbank testbar halten
- Start der Anwendung: `python -m erd_editor.main`

## Nutzer
**Name:** Michael
