"""Tabelleneditor: Name, Kommentar und Spalten einer Modell-Tabelle bearbeiten."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from erd_editor.model import Column, Dialect, Index, IndexKind, Table
from erd_editor.model.types import COMMON_TYPES, ID_TYPES

NAME, TYPE, NULLABLE, PK, AUTO, DEFAULT, COMMENT = range(7)
HEADERS = ["Name", "Datentyp", "NULL", "PK", "Auto", "Default", "Kommentar"]
ORIGINAL = Qt.ItemDataRole.UserRole  # ursprünglicher Spaltenname (für Umbenennungen)


class TableDialog(QDialog):
    """Neue Tabelle anlegen oder vorhandene bearbeiten.

    `table=None` legt eine neue Tabelle mit einer Schlüsselspalte `id` an.
    """

    def __init__(
        self,
        dialect: Dialect,
        table: Table | None = None,
        taken_names: set[str] | None = None,
        location: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.dialect = dialect
        self._original = table
        self._taken = set(taken_names or set()) - ({table.name} if table else set())
        self.setWindowTitle("Tabelle bearbeiten" if table else "Neue Tabelle")
        self.resize(820, 480)

        self.name = QLineEdit(table.name if table else "")
        self.name.setPlaceholderText("z. B. kunde")
        self.name.textChanged.connect(self._validate)
        self.comment = QLineEdit(table.comment or "" if table else "")
        form = QFormLayout()
        if location:
            form.addRow("Ort:", QLabel(location))
        form.addRow("Name:", self.name)
        form.addRow("Kommentar:", self.comment)

        self.grid = QTableWidget(0, len(HEADERS))
        self.grid.setHorizontalHeaderLabels(HEADERS)
        self.grid.verticalHeader().setVisible(False)
        self.grid.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.grid.horizontalHeader()
        header.setSectionResizeMode(NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(TYPE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COMMENT, QHeaderView.ResizeMode.Stretch)
        for col in (NULLABLE, PK, AUTO):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.grid.itemChanged.connect(self._item_changed)

        if table:
            for c in table.columns:
                self.add_row(c, table.is_primary_key_column(c.name), original=c.name)
        else:
            self.add_row(Column("id", ID_TYPES[dialect], nullable=False, auto_increment=True), pk=True)

        buttons = QHBoxLayout()
        for text, slot in [("Spalte hinzufügen", self.add_empty_row), ("Entfernen", self.remove_row),
                           ("Nach oben", lambda: self.move_row(-1)), ("Nach unten", lambda: self.move_row(+1))]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        buttons.addStretch(1)

        self.message = QLabel()
        self.message.setStyleSheet("color:#B42318")
        self.box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.box.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        self.box.accepted.connect(self.accept)
        self.box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.grid, 1)
        layout.addLayout(buttons)
        layout.addWidget(self.message)
        layout.addWidget(self.box)
        self._validate()

    # ------------------------------------------------------------------ Zeilen

    def add_row(self, column: Column, pk: bool = False, original: str | None = None) -> int:
        self.grid.blockSignals(True)
        row = self.grid.rowCount()
        self.grid.insertRow(row)
        name = QTableWidgetItem(column.name)
        name.setData(ORIGINAL, original)
        self.grid.setItem(row, NAME, name)
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(COMMON_TYPES[self.dialect])
        combo.setCurrentText(column.data_type)
        combo.currentTextChanged.connect(self._validate)
        self.grid.setCellWidget(row, TYPE, combo)
        for col, checked in ((NULLABLE, column.nullable), (PK, pk), (AUTO, column.auto_increment)):
            item = QTableWidgetItem()
            item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self.grid.setItem(row, col, item)
        self.grid.setItem(row, DEFAULT, QTableWidgetItem(column.default or ""))
        self.grid.setItem(row, COMMENT, QTableWidgetItem(column.comment or ""))
        self.grid.blockSignals(False)
        self._validate()
        return row

    def add_empty_row(self) -> None:
        row = self.add_row(Column("", COMMON_TYPES[self.dialect][0], nullable=True))
        self.grid.setCurrentCell(row, NAME)
        self.grid.editItem(self.grid.item(row, NAME))

    def remove_row(self) -> None:
        row = self.grid.currentRow()
        if row >= 0:
            self.grid.removeRow(row)
            self._validate()

    def move_row(self, delta: int) -> None:
        row = self.grid.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < self.grid.rowCount():
            return
        columns = self._rows()
        columns[row], columns[target] = columns[target], columns[row]
        self.grid.setRowCount(0)
        for column, pk, original in columns:
            self.add_row(column, pk, original)
        self.grid.setCurrentCell(target, NAME)

    def _item_changed(self, item: QTableWidgetItem) -> None:
        # Primärschlüsselspalten sind nie NULL-fähig
        if item.column() == PK and item.checkState() == Qt.CheckState.Checked:
            self.grid.item(item.row(), NULLABLE).setCheckState(Qt.CheckState.Unchecked)
        self._validate()

    # ------------------------------------------------------------------ Ergebnis

    def _checked(self, row: int, col: int) -> bool:
        item = self.grid.item(row, col)
        return item is not None and item.checkState() == Qt.CheckState.Checked

    def _text(self, row: int, col: int) -> str:
        item = self.grid.item(row, col)
        return item.text().strip() if item else ""

    def _rows(self) -> list[tuple[Column, bool, str | None]]:
        rows = []
        for r in range(self.grid.rowCount()):
            pk = self._checked(r, PK)
            column = Column(
                name=self._text(r, NAME),
                data_type=self.grid.cellWidget(r, TYPE).currentText().strip(),
                nullable=self._checked(r, NULLABLE) and not pk,
                default=self._text(r, DEFAULT) or None,
                comment=self._text(r, COMMENT) or None,
                auto_increment=self._checked(r, AUTO),
            )
            rows.append((column, pk, self.grid.item(r, NAME).data(ORIGINAL)))
        return rows

    def table(self) -> Table:
        """Ergebnis: neue Tabelle; Indexe/FKs der alten Tabelle bleiben (mit alten Spaltennamen)."""
        rows = self._rows()
        name = self.name.text().strip()
        pk_cols = tuple(c.name for c, pk, _ in rows if pk)
        old = self._original
        old_pk = old.primary_key if old else None
        pk_name = old_pk.name if old_pk else ("PRIMARY" if self.dialect is Dialect.MYSQL else f"{name}_pkey")
        indexes = [Index(pk_name, IndexKind.PRIMARY, pk_cols)] if pk_cols else []
        if old:
            indexes += [i for i in old.indexes if i.kind is not IndexKind.PRIMARY]
        return Table(
            name=name,
            columns=[c for c, _, _ in rows],
            indexes=indexes,
            foreign_keys=list(old.foreign_keys) if old else [],
            comment=self.comment.text().strip() or None,
        )

    def column_renames(self) -> dict[str, str]:
        return {orig: c.name for c, _, orig in self._rows() if orig and orig != c.name}

    def _validate(self, *_args) -> None:
        if not hasattr(self, "box"):  # Aufbau noch nicht abgeschlossen
            return
        problem = ""
        name = self.name.text().strip()
        rows = self._rows() if self.grid.rowCount() else []
        names = [c.name for c, _, _ in rows]
        if not name:
            problem = "Bitte einen Tabellennamen eingeben."
        elif name in self._taken:
            problem = f"Eine Tabelle „{name}“ gibt es dort schon."
        elif not rows:
            problem = "Eine Tabelle braucht mindestens eine Spalte."
        elif any(not n for n in names):
            problem = "Jede Spalte braucht einen Namen."
        elif len(names) != len(set(names)):
            problem = "Spaltennamen müssen eindeutig sein."
        elif any(not c.data_type for c, _, _ in rows):
            problem = "Jede Spalte braucht einen Datentyp."
        self.message.setText(problem)
        self.box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not problem)
