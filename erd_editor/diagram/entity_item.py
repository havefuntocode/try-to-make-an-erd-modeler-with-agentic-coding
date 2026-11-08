"""Darstellung einer Tabelle als Box: Kopfzeile, Kommentar, Spaltenliste, Indexbereich.

Was gezeigt wird, steuern die `DisplayOptions` (alle/nur Schlüssel-/keine
Spalten, Datentypen, Indexbereich, Tabellenkommentar).
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsSceneHoverEvent,
    QStyleOptionGraphicsItem,
    QWidget,
)

from erd_editor.diagram.icons import draw_icon
from erd_editor.diagram.markers import INDEXED_ICON, ColumnMarker, column_marker, index_label
from erd_editor.diagram.style import EntityStyle
from erd_editor.model import Column, ColumnMode, DisplayOptions, Index, IndexKind, Table, TableRef

MAX_COMMENT_WIDTH = 280.0  # längere Tabellenkommentare werden gekürzt (…)
MAX_TOOLTIP_COLUMNS = 25


@dataclass
class _ColumnRow:
    column: Column
    marker: ColumnMarker
    is_pk: bool
    top: float


@dataclass
class _IndexRow:
    index: Index
    kind: str
    label: str
    top: float


def visible_columns(table: Table, mode: ColumnMode) -> list[Column]:
    """Spalten, die bei der gewählten Option angezeigt werden."""
    if mode is ColumnMode.NONE:
        return []
    if mode is ColumnMode.KEYS:
        return [
            c for c in table.columns
            if table.is_primary_key_column(c.name) or table.is_foreign_key_column(c.name)
        ]
    return list(table.columns)


def hidden_label(hidden: int, mode: ColumnMode) -> str:
    """Hinweiszeile für ausgeblendete Spalten."""
    noun = "Spalte" if hidden == 1 else "Spalten"
    return f"{hidden} {noun}" if mode is ColumnMode.NONE else f"+ {hidden} weitere {noun}"


class EntityItem(QGraphicsObject):
    """Verschiebbare Box für eine Tabelle.

    Die Geometrie wird beim Erzeugen und bei geänderten Darstellungsoptionen
    berechnet; ändert sich die Tabelle selbst, wird ein neues Item erzeugt.
    """

    def __init__(
        self,
        ref: TableRef,
        table: Table,
        style: EntityStyle | None = None,
        options: DisplayOptions | None = None,
    ) -> None:
        super().__init__()
        self.ref = ref
        self.table = table
        self.style = style or EntityStyle()
        self.options = options or DisplayOptions()
        self.square_corners = False  # IDEF1X: unabhängige Entitäten eckig
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self._compute_layout()

    def set_options(self, options: DisplayOptions) -> None:
        """Darstellungsoptionen ändern; Größe wird neu berechnet."""
        if options == self.options:
            return
        self.prepareGeometryChange()
        self.options = options
        self._compute_layout()
        self.update()

    # ------------------------------------------------------------------ Geometrie

    @property
    def comment_font(self) -> QFont:
        font = QFont(self.style.index_font)
        font.setItalic(True)
        return font

    def _compute_layout(self) -> None:
        s, o = self.style, self.options
        fm_header = QFontMetricsF(s.header_font)
        fm_col = QFontMetricsF(s.column_font)
        fm_pk = QFontMetricsF(s.pk_font)
        fm_idx = QFontMetricsF(s.index_font)
        fm_comment = QFontMetricsF(self.comment_font)
        p = s.padding

        self._header_h = fm_header.height() + 1.5 * p
        self._row_h = max(fm_col.height(), s.icon_size) + s.row_spacing
        self._name_x = p + s.icon_size + s.gap + s.marker_size + s.gap
        y = self._header_h + p / 2

        # Tabellenkommentar (Breite wird unten festgelegt)
        comment = self.table.comment if o.show_comments else None
        self._comment_top: float | None = None
        if comment:
            self._comment_top = y
            y += fm_comment.height() + p / 2

        # Spalten
        shown = visible_columns(self.table, o.columns)
        self._hidden = [c for c in self.table.columns if c not in shown]
        self._columns: list[_ColumnRow] = []
        name_w = type_w = 0.0
        for col in shown:
            is_pk = self.table.is_primary_key_column(col.name)
            self._columns.append(_ColumnRow(col, column_marker(self.table, col), is_pk, y))
            name_w = max(name_w, (fm_pk if is_pk else fm_col).horizontalAdvance(col.name))
            if o.show_types:
                type_w = max(type_w, fm_col.horizontalAdvance(col.data_type))
            y += self._row_h
        self._type_x = self._name_x + name_w + (s.type_gap if o.show_types else 0)
        width = self._type_x + type_w + p if shown else 0.0

        # Hinweis auf ausgeblendete Spalten
        self._more_top: float | None = None
        self._more_text = ""
        if self._hidden:
            self._more_top = y
            self._more_text = hidden_label(len(self._hidden), o.columns)
            width = max(width, p + fm_idx.horizontalAdvance(self._more_text) + p)
            y += self._row_h
        columns_bottom = y

        # Indexbereich
        self._index_top: float | None = None
        self._indexes: list[_IndexRow] = []
        self._index_row_h = fm_idx.height() + 2
        if o.show_indexes and self.table.indexes:
            self._index_top = columns_bottom + p / 2
            y = self._index_top + p / 2
            kind_w = label_w = 0.0
            # Primärschlüssel zuerst, sonst Reihenfolge der Datenbank
            ordered = sorted(self.table.indexes, key=lambda i: i.kind is not IndexKind.PRIMARY)
            for index in ordered:
                kind, label = index_label(index)
                self._indexes.append(_IndexRow(index, kind, label, y))
                kind_w = max(kind_w, fm_idx.horizontalAdvance(kind))
                label_w = max(label_w, fm_idx.horizontalAdvance(label))
                y += self._index_row_h
            self._index_label_x = p + kind_w + s.gap * 2
            width = max(width, self._index_label_x + label_w + p)
            height = y + p / 2
        else:
            height = columns_bottom + p / 2

        width = max(width, s.min_width, 2 * p + fm_header.horizontalAdvance(self.table.name))

        # Kommentar darf die Box bis MAX_COMMENT_WIDTH verbreitern, darüber wird gekürzt
        self._comment_text = ""
        if comment:
            available = max(width - 2 * p, min(fm_comment.horizontalAdvance(comment), MAX_COMMENT_WIDTH))
            width = max(width, available + 2 * p)
            self._comment_text = fm_comment.elidedText(comment, Qt.TextElideMode.ElideRight, available)

        self._rect = QRectF(0, 0, width, height)

    @property
    def rect(self) -> QRectF:
        """Umriss der Box in Item-Koordinaten (ohne Rand für die Auswahl)."""
        return QRectF(self._rect)

    def boundingRect(self) -> QRectF:
        m = self.style.selected_border_width
        return self._rect.adjusted(-m, -m, m, m)

    def shape(self) -> QPainterPath:
        return self._outline()

    def _outline(self) -> QPainterPath:
        path = QPainterPath()
        radius = 0.0 if self.square_corners else self.style.radius
        path.addRoundedRect(self._rect, radius, radius)
        return path

    def set_square_corners(self, square: bool) -> None:
        if square != self.square_corners:
            self.prepareGeometryChange()
            self.square_corners = square
            self.update()

    def column_anchor_y(self, column_name: str) -> float | None:
        """Vertikale Mitte einer sichtbaren Spaltenzeile (Item-Koordinaten) – Andockpunkt für Beziehungen.

        Ausgeblendete Spalten liefern None; die Linie dockt dann mittig an.
        """
        for row in self._columns:
            if row.column.name == column_name:
                return row.top + self._row_h / 2
        return None

    # ------------------------------------------------------------------ Zeichnen

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        s = self.style
        p = s.padding
        w = self._rect.width()
        outline = self._outline()
        left = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

        # Flächen
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(s.body)
        painter.drawPath(outline)
        painter.setClipPath(outline)
        painter.fillRect(QRectF(0, 0, w, self._header_h), s.header)
        if self._index_top is not None:
            painter.fillRect(QRectF(0, self._index_top, w, self._rect.height() - self._index_top), s.index_area)
        painter.restore()

        # Trennlinien
        painter.setPen(QPen(s.separator, 1))
        painter.drawLine(QPointF(0, self._header_h), QPointF(w, self._header_h))
        if self._index_top is not None:
            painter.drawLine(QPointF(0, self._index_top), QPointF(w, self._index_top))

        # Kopfzeile
        painter.setFont(s.header_font)
        painter.setPen(s.header_text)
        painter.drawText(QRectF(p, 0, w - 2 * p, self._header_h), Qt.AlignmentFlag.AlignVCenter, self.table.name)

        # Tabellenkommentar
        if self._comment_top is not None:
            painter.setFont(self.comment_font)
            painter.setPen(s.type_text)
            h = QFontMetricsF(self.comment_font).height()
            painter.drawText(QRectF(p, self._comment_top, w - 2 * p, h), left, self._comment_text)

        # Spalten
        for row in self._columns:
            icon_y = row.top + (self._row_h - s.icon_size) / 2
            draw_icon(painter, row.marker.icon.value, QRectF(p, icon_y, s.icon_size, s.icon_size))
            if row.marker.indexed:
                mx = p + s.icon_size + s.gap
                my = row.top + (self._row_h - s.marker_size) / 2
                draw_icon(painter, INDEXED_ICON, QRectF(mx, my, s.marker_size, s.marker_size))
            painter.setFont(s.pk_font if row.is_pk else s.column_font)
            painter.setPen(s.text)
            painter.drawText(QRectF(self._name_x, row.top, w - self._name_x - p, self._row_h), left, row.column.name)
            if self.options.show_types:
                painter.setFont(s.column_font)
                painter.setPen(s.type_text)
                painter.drawText(QRectF(self._type_x, row.top, w - self._type_x - p, self._row_h), left,
                                 row.column.data_type)

        # Hinweis auf ausgeblendete Spalten
        if self._more_top is not None:
            painter.setFont(s.index_font)
            painter.setPen(s.type_text)
            painter.drawText(QRectF(p, self._more_top, w - 2 * p, self._row_h), left, self._more_text)

        # Indexe
        painter.setFont(s.index_font)
        painter.setPen(s.index_text)
        for row in self._indexes:
            h = self._index_row_h
            painter.drawText(QRectF(p, row.top, self._index_label_x - p, h), left, row.kind)
            painter.drawText(QRectF(self._index_label_x, row.top, w - self._index_label_x - p, h), left, row.label)

        # Rahmen
        selected = self.isSelected()
        pen = QPen(s.selected_border if selected else s.border, s.selected_border_width if selected else s.border_width)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(outline)

    # ------------------------------------------------------------------ Interaktion

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.setZValue(1 if value else 0)  # ausgewählte Box nach vorn
        elif change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            scene = self.scene()
            if scene is not None and hasattr(scene, "entity_moved"):
                scene.entity_moved(self)  # Linien nachführen
        return super().itemChange(change, value)

    def tooltip_at(self, pos: QPointF) -> str:
        """Tooltip-Text (HTML) für eine Position in Item-Koordinaten."""
        y = pos.y()
        if y < self._header_h or (self._comment_top is not None and y < self._comment_top + self._row_h):
            return _with_comment(f"<b>{escape(self.ref.key)}</b>", self.table.comment)
        for row in self._columns:
            if row.top <= y < row.top + self._row_h:
                return column_tooltip(row.column)
        if self._more_top is not None and self._more_top <= y < self._more_top + self._row_h:
            names = [escape(c.name) for c in self._hidden[:MAX_TOOLTIP_COLUMNS]]
            if len(self._hidden) > MAX_TOOLTIP_COLUMNS:
                names.append("…")
            return "<b>Ausgeblendet:</b><br>" + "<br>".join(names)
        return ""

    def hoverMoveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        self.setToolTip(self.tooltip_at(event.pos()))
        super().hoverMoveEvent(event)


def column_tooltip(c: Column) -> str:
    parts = [f"<b>{escape(c.name)}</b> {escape(c.data_type)}", "NULL" if c.nullable else "NOT NULL"]
    if c.default is not None:
        parts.append("Default: ''" if c.default == "" else f"Default: {escape(c.default)}")
    if c.auto_increment:
        parts.append("AUTO_INCREMENT")
    return _with_comment(" · ".join(parts), c.comment)


def _with_comment(text: str, comment: str | None) -> str:
    return f"{text}<br><i>{escape(comment)}</i>" if comment else text
