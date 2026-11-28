"""Gemerkte Orte für die Datei-Dialoge: letzter Ordner je Zweck und zuletzt geöffnete Diagramme.

Alles liegt in den Programmeinstellungen (`toolbar.app_settings()`, Gruppe `files`):

- `files/dir_<zweck>`: letzter Ordner, getrennt nach Zweck (`diagram`, `export`, `sql`),
  damit z. B. PDF-Exporte nicht den Ordner der Diagrammdateien verstellen
- `files/recent`: zuletzt geöffnete oder gespeicherte Diagrammdateien, neueste zuerst
"""

from __future__ import annotations

from pathlib import Path

from erd_editor.ui import toolbar

DIAGRAM = "diagram"
EXPORT = "export"
SQL = "sql"
MAX_RECENT = 8


def _key(purpose: str) -> str:
    return f"files/dir_{purpose}"


def last_dir(purpose: str) -> Path | None:
    """Zuletzt benutzter Ordner für diesen Zweck – nur, wenn es ihn noch gibt."""
    value = toolbar.app_settings().value(_key(purpose))
    if not value:
        return None
    folder = Path(str(value))
    return folder if folder.is_dir() else None


def remember_dir(purpose: str, file_path: str | Path) -> None:
    """Ordner der gewählten Datei für den nächsten Dialog dieses Zwecks merken."""
    folder = Path(file_path).resolve().parent
    toolbar.app_settings().setValue(_key(purpose), str(folder))


def _normalized(path: str | Path) -> str:
    return str(Path(path).resolve())


def recent_files() -> list[Path]:
    """Zuletzt benutzte Diagrammdateien, neueste zuerst (auch solche, die es nicht mehr gibt)."""
    value = toolbar.app_settings().value("files/recent")
    if not value:
        return []
    # QSettings liefert bei einem einzelnen Eintrag (INI-Format) einen String statt einer Liste
    items = [value] if isinstance(value, str) else list(value)
    return [Path(str(item)) for item in items if item][:MAX_RECENT]


def _store_recent(paths: list[Path]) -> None:
    toolbar.app_settings().setValue("files/recent", [str(p) for p in paths[:MAX_RECENT]])


def add_recent_file(path: str | Path) -> None:
    """Datei an die Spitze der Liste setzen (Doppelte entfallen, Groß-/Kleinschreibung egal)."""
    new = Path(_normalized(path))
    rest = [p for p in recent_files() if str(p).casefold() != str(new).casefold()]
    _store_recent([new, *rest])


def remove_recent_file(path: str | Path) -> None:
    gone = _normalized(path).casefold()
    _store_recent([p for p in recent_files() if str(p).casefold() != gone])


def clear_recent_files() -> None:
    toolbar.app_settings().remove("files/recent")
