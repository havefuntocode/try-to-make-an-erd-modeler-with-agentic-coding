"""Symbolleisten-Icons (24×24, Strichstil) nach erd_editor/resources/toolbar schreiben.

Aufruf aus dem Projektordner:  .venv/Scripts/python tools/make_toolbar_icons.py
Farben und Formen hier ändern und neu erzeugen – die SVGs sind das Ergebnis.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parent.parent
out = root / "erd_editor" / "resources" / "toolbar"
out.mkdir(parents=True, exist_ok=True)

BLUE = "#1F4E99"  # wie das Anwendungssymbol
TEAL = "#0E8C7A"  # „neu“-Plus (wie die Index-Markierung im Diagramm)
NOTE_FILL, NOTE_LINE = "#FFF6C4", "#B39A3E"  # wie die Notizen
RED = "#C2410C"


def svg(body: str, stroke: str = BLUE) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24" '
        f'fill="none" stroke="{stroke}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        f"{body}</svg>\n"
    )


# „Neu“-Abzeichen rechts unten: weiße Scheibe, türkises Plus
PLUS_BADGE = (
    '<circle cx="18.5" cy="18.5" r="5" fill="#FFFFFF" stroke="none"/>'
    f'<path d="M18.5 15.5v6M15.5 18.5h6" stroke="{TEAL}" stroke-width="2"/>'
)
# Datenbank (Zylinder) links, Platz für Abzeichen/Pfeil rechts
DB = (
    '<ellipse cx="9" cy="5" rx="6" ry="2.5"/>'
    '<path d="M3 5v13c0 1.4 2.7 2.5 6 2.5s6-1.1 6-2.5V5"/>'
    '<path d="M3 11.5c0 1.4 2.7 2.5 6 2.5s6-1.1 6-2.5"/>'
)
TABLE = '<rect x="2.5" y="3.5" width="15" height="14" rx="2"/><path d="M2.5 8h15M8 8v9.5"/>'
VIEW = ('<rect x="2.5" y="3.5" width="15" height="14" rx="2" stroke-dasharray="3 2"/>'
        '<path d="M2.5 8h15M8 8v9.5"/>')
DOC = '<path d="M14 2.5H6.5a2 2 0 0 0-2 2v15a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V8z"/><path d="M14 2.5V8h5.5"/>'
LENS = '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M20.5 20.5l-5.4-5.4"/>'


def label(text: str, color: str) -> str:
    """Farbiges Etikett mit Kürzel (PDF, SQL) über dem Dokument."""
    return (
        f'<rect x="2" y="11.5" width="15" height="7.5" rx="1.5" fill="{color}" stroke="none"/>'
        f'<text x="9.5" y="17.4" font-family="Segoe UI, Arial, sans-serif" font-size="5.6" '
        f'font-weight="700" fill="#FFFFFF" stroke="none" text-anchor="middle">{text}</text>'
    )


ICONS = {
    # Datei
    "new": DOC + f'<path d="M12 11.5v6M9 14.5h6" stroke="{TEAL}" stroke-width="2"/>',
    "open": '<path d="M3 7.5a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'
            '<path d="M3 11h18"/>',
    "save": '<path d="M5 3h11.5L21 7.5V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/>'
            '<path d="M7.5 3v5h8V3"/><rect x="7" y="13" width="10" height="8" rx="1"/>',
    "db_read": DB + f'<path d="M19.5 7.5v10M16.5 14.5l3 3 3-3" stroke="{TEAL}" stroke-width="2"/>',
    "reload": '<path d="M20 11a8 8 0 0 0-14.4-4.6L4 8"/><path d="M4 3.5V8h4.5"/>'
              '<path d="M4 13a8 8 0 0 0 14.4 4.6L20 16"/><path d="M20 20.5V16h-4.5"/>',
    # Bearbeiten
    "undo": '<path d="M9 14.5 4 9.5l5-5"/><path d="M4 9.5h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    "redo": '<path d="M15 14.5l5-5-5-5"/><path d="M20 9.5H9.5a5.5 5.5 0 0 0 0 11H13"/>',
    # Modell
    "db_new": DB + PLUS_BADGE,
    "table_new": TABLE + PLUS_BADGE,
    "view_new": VIEW + PLUS_BADGE,
    "relation": '<rect x="2" y="2" width="9" height="6.5" rx="1.5"/><rect x="15" y="14.5" width="7" height="7.5" rx="1.5"/>'
                '<path d="M6.5 8.5v9.75H15"/><path d="M4.75 11.25h3.5"/>'
                f'<path d="M10.5 18.25l4.5-3.25M10.5 18.25l4.5 3.25" stroke="{TEAL}" stroke-width="1.8"/>',
    "note": f'<path d="M4 3.5h11.5L20 8v12.5H4z" fill="{NOTE_FILL}" stroke="{NOTE_LINE}"/>'
            f'<path d="M15.5 3.5V8H20" stroke="{NOTE_LINE}"/>'
            f'<path d="M7.5 11.5h9M7.5 15h6" stroke="{NOTE_LINE}"/>',
    # Ansicht
    "zoom_in": LENS + '<path d="M10.5 7.5v6M7.5 10.5h6"/>',
    "zoom_out": LENS + '<path d="M7.5 10.5h6"/>',
    "fit": '<path d="M3.5 8.5V4.5a1 1 0 0 1 1-1h4"/><path d="M15.5 3.5h4a1 1 0 0 1 1 1v4"/>'
           '<path d="M20.5 15.5v4a1 1 0 0 1-1 1h-4"/><path d="M8.5 20.5h-4a1 1 0 0 1-1-1v-4"/>'
           '<rect x="8.5" y="8.5" width="7" height="7" rx="1"/>',
    "auto_layout": '<rect x="2.5" y="3" width="7.5" height="5" rx="1"/><rect x="14" y="3" width="7.5" height="5" rx="1"/>'
                   '<rect x="8.25" y="16" width="7.5" height="5" rx="1"/>'
                   '<path d="M6.25 8v3.5h11.5V8M12 11.5V16"/>',
    # Export
    "pdf": DOC + label("PDF", RED),
    "sql": DOC + label("SQL", TEAL),
}

for name, body in ICONS.items():
    (out / f"{name}.svg").write_text(svg(body), encoding="utf-8")
print(len(ICONS), "Icons nach", out)
