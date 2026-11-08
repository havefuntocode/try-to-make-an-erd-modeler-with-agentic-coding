"""Schema als heller innerer Rahmen im Datenbank-Behälter (PostgreSQL, SQL Server).

Verhält sich wie der Datenbank-Behälter – Größe folgt den Tabellen,
Verschieben über die Kopfzeile, Aufziehen an den Kanten –, ist aber
heller, kleiner beschriftet und gestrichelt umrandet.
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from erd_editor.diagram.container_item import ContainerItem
from erd_editor.diagram.palette import ContainerColors
from erd_editor.model import Database, Schema


def schema_colors(db_colors: ContainerColors) -> ContainerColors:
    """Hellere Variante der Datenbankfarben."""
    band = QColor(db_colors.band).lighter(104).name()
    return ContainerColors(f"{db_colors.name} (Schema)", "#FFFFFF", band, db_colors.entity_header, db_colors.border)


class SchemaItem(ContainerItem):
    PADDING = 16.0
    RADIUS = 7.0
    BORDER_WIDTH = 1.2
    TITLE_PT = 9.5
    INFO_PT = 7.5
    BORDER_STYLE = Qt.PenStyle.DashLine
    Z = -1.5  # vor dem Datenbank-Behälter, hinter Linien und Entitäten

    def __init__(self, database: Database, schema: Schema, db_colors: ContainerColors, scale: float = 1.0) -> None:
        self.schema = schema  # vor super(), weil dort schon der Tooltip entsteht
        super().__init__(database, schema_colors(db_colors), 0, scale)

    @property
    def title(self) -> str:
        return self.schema.name or ""

    @property
    def info_text(self) -> str:
        n = len(self.schema.tables)
        return f"Schema · {n} Tabelle{'' if n == 1 else 'n'}"

    def _tooltip(self) -> str:
        return "<br>".join(
            [
                f"<b>{escape(self.database.name)}.{escape(self.title)}</b>",
                escape(self.info_text),
                "Kopfzeile ziehen: verschieben · Rand ziehen: größer aufziehen",
            ]
        )
