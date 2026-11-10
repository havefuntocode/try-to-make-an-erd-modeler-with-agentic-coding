"""Tabelleneditor: Name, Kommentar, Spalten und Indexe einer Modell-Tabelle bearbeiten."""

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
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from erd_editor.model import Column, Dialect, Index, IndexKind, Table
from erd_editor.model.types import COMMON_TYPES, ID_TYPES

NAME, TYPE, NULLABLE, PK, AUTO, DEFAULT, COMMENT = range(7)
HEADERS = ["Name", "Datentyp", "NULL", "PK", "Auto", "Default", "Kommentar"]
ORIGINAL = Qt.ItemDataRole.UserRole  # ursprünglicher Spaltenname (für Umbenennungen)
KEY = Qt.ItemDataRole.UserRole + 1  # stabiler Zeilenschlüssel (Indexe überleben Umbenennen/Verschieben)

IDX_NAME, IDX_KIND, IDX_COLUMNS = range(3)
IDX_HEADERS = ["Name", "Art", "Spalten"]

# Indexarten, die der Editor je Dialekt anbietet (PK läuft über die Spalte „PK“)
INDEX_KINDS = {
    Dialect.MYSQL: [IndexKind.UNIQUE, IndexKind.INDEX, IndexKind.FULLTEXT, IndexKind.SPATIAL],
    Dialect.POSTGRESQL: [IndexKind.UNIQUE, IndexKind.INDEX],
    Dialect.MSSQL: [IndexKind.UNIQUE, IndexKind.INDEX],
}


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
        self._next_key = 0
        self._index_keys: list[list[int]] = []  # je Indexzeile: Spaltenschlüssel in Indexreihenfolge
        self.setWindowTitle("Tabelle bearbeiten" if table else "Neue Tabelle")
        self.resize(820, 520)

        self.name = QLineEdit(table.name if table else "")
        self.name.setPlaceholderText("z. B. kunde")
        self.name.textChanged.connect(self._validate)
        self.comment = QLineEdit(table.comment or "" if table else "")
        form = QFormLayout()
        if location:
            form.addRow("Ort:", QLabel(location))
        form.addRow("Name:", self.name)
        form.addRow("Kommentar:", self.comment)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_columns_page(), "Spalten")
        self.tabs.addTab(self._build_indexes_page(), "Indexe")

        if table:
            for c in table.columns:
                self.add_row(c, table.is_primary_key_column(c.name), original=c.name)
            by_original = {self.grid.item(r, NAME).data(ORIGINAL): self._row_key(r) for r in range(self.grid.rowCount())}
            for index in table.indexes:
                if index.kind is IndexKind.PRIMARY:
                    continue
                keys = [by_original[c] for c in index.columns if c in by_original]
                if keys:
                    self.add_index(index.name, index.kind, keys)
        else:
            self.add_row(Column("id", ID_TYPES[dialect], nullable=False, auto_increment=True), pk=True)

        self.message = QLabel()
        self.message.setStyleSheet("color:#B42318")
        self.box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.box.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        self.box.accepted.connect(self.accept)
        self.box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.message)
        layout.addWidget(self.box)
        if self.idx_grid.rowCount():
            self.idx_grid.setCurrentCell(0, IDX_NAME)
        self._validate()

    # ------------------------------------------------------------------ Aufbau

    def _build_columns_page(self) -> QWidget:
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

        buttons = QHBoxLayout()
        for text, slot in [("Spalte hinzufügen", self.add_empty_row), ("Entfernen", self.remove_row),
                           ("Nach oben", lambda: self.move_row(-1)), ("Nach unten", lambda: self.move_row(+1))]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        buttons.addStretch(1)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(self.grid, 1)
        layout.addLayout(buttons)
        return page

    def _build_indexes_page(self) -> QWidget:
        self.idx_grid = QTableWidget(0, len(IDX_HEADERS))
        self.idx_grid.setHorizontalHeaderLabels(IDX_HEADERS)
        self.idx_grid.verticalHeader().setVisible(False)
        self.idx_grid.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.idx_grid.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.idx_grid.horizontalHeader()
        header.setSectionResizeMode(IDX_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(IDX_KIND, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(IDX_COLUMNS, QHeaderView.ResizeMode.Stretch)
        self.idx_grid.itemChanged.connect(self._validate)
        self.idx_grid.currentCellChanged.connect(lambda *_: self._fill_index_columns())

        index_buttons = QHBoxLayout()
        for text, slot in [("Index hinzufügen", lambda: self.add_index()), ("Entfernen", self.remove_index)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            index_buttons.addWidget(b)
        index_buttons.addStretch(1)

        left = QVBoxLayout()
        left.addWidget(self.idx_grid, 1)
        left.addLayout(index_buttons)
        hint = QLabel("Der Primärschlüssel wird im Reiter „Spalten“ über „PK“ festgelegt.")
        hint.setStyleSheet("color:palette(mid)")
        left.addWidget(hint)

        self.idx_columns = QListWidget()
        self.idx_columns.itemChanged.connect(self._index_columns_changed)
        order = QHBoxLayout()
        for text, delta in [("Nach oben", -1), ("Nach unten", +1)]:
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, d=delta: self.move_index_column(d))
            order.addWidget(b)
        right = QVBoxLayout()
        right.addWidget(QLabel("Spalten des Index\n(Reihenfolge = Indexreihenfolge):"))
        right.addWidget(self.idx_columns, 1)
        right.addLayout(order)

        page = QWidget()
        layout = QHBoxLayout(page)
        layout.addLayout(left, 3)
        layout.addLayout(right, 2)
        return page

    # ------------------------------------------------------------------ Spalten

    def _row_key(self, row: int) -> int:
        return self.grid.item(row, NAME).data(KEY)

    def _names_by_key(self) -> dict[int, str]:
        return {self._row_key(r): self._text(r, NAME) for r in range(self.grid.rowCount())}

    def add_row(self, column: Column, pk: bool = False, original: str | None = None, key: int | None = None) -> int:
        if key is None:
            key = self._next_key
            self._next_key += 1
        self.grid.blockSignals(True)
        row = self.grid.rowCount()
        self.grid.insertRow(row)
        name = QTableWidgetItem(column.name)
        name.setData(ORIGINAL, original)
        name.setData(KEY, key)
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
        self._refresh_indexes()
        self._validate()
        return row

    def add_empty_row(self) -> None:
        row = self.add_row(Column("", COMMON_TYPES[self.dialect][0], nullable=True))
        self.grid.setCurrentCell(row, NAME)
        self.grid.editItem(self.grid.item(row, NAME))

    def remove_row(self) -> None:
        row = self.grid.currentRow()
        if row < 0:
            return
        key = self._row_key(row)
        self.grid.removeRow(row)
        # Spalte aus den Indexen nehmen; Indexe ohne Spalten entfallen
        for i in reversed(range(len(self._index_keys))):
            keys = [k for k in self._index_keys[i] if k != key]
            if keys:
                self._index_keys[i] = keys
            else:
                self._index_keys.pop(i)
                self.idx_grid.removeRow(i)
        self._refresh_indexes()
        self._validate()

    def move_row(self, delta: int) -> None:
        row = self.grid.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < self.grid.rowCount():
            return
        columns = [(*r, self._row_key(i)) for i, r in enumerate(self._rows())]
        columns[row], columns[target] = columns[target], columns[row]
        self.grid.setRowCount(0)
        for column, pk, original, key in columns:
            self.add_row(column, pk, original, key)
        self.grid.setCurrentCell(target, NAME)

    def _item_changed(self, item: QTableWidgetItem) -> None:
        # Primärschlüsselspalten sind nie NULL-fähig
        if item.column() == PK and item.checkState() == Qt.CheckState.Checked:
            self.grid.item(item.row(), NULLABLE).setCheckState(Qt.CheckState.Unchecked)
        if item.column() == NAME:
            self._refresh_indexes()
        self._validate()

    # ------------------------------------------------------------------ Indexe

    def _kinds(self, current: IndexKind | None = None) -> list[IndexKind]:
        kinds = list(INDEX_KINDS[self.dialect])
        if current is not None and current not in kinds:
            kinds.append(current)  # z. B. eingelesene Art, die der Dialekt-Editor sonst nicht anbietet
        return kinds

    def _index_names(self) -> list[str]:
        return [self.idx_grid.item(r, IDX_NAME).text().strip() for r in range(self.idx_grid.rowCount())]

    def _pk_name(self) -> str:
        old_pk = self._original.primary_key if self._original else None
        if old_pk:
            return old_pk.name
        return "PRIMARY" if self.dialect is Dialect.MYSQL else f"{self.name.text().strip()}_pkey"

    def add_index(self, name: str | None = None, kind: IndexKind = IndexKind.INDEX, keys: list[int] | None = None) -> int:
        """Indexzeile anfügen; ohne Angaben mit Vorschlagsnamen und der markierten Spalte."""
        if name is None:
            base = f"idx_{self.name.text().strip() or 'tabelle'}"
            taken = set(self._index_names()) | {self._pk_name()}
            name, n = base, 2
            while name in taken:
                name, n = f"{base}_{n}", n + 1
        if keys is None:
            row = self.grid.currentRow()
            keys = [self._row_key(row)] if row >= 0 else []
        self.idx_grid.blockSignals(True)
        row = self.idx_grid.rowCount()
        self.idx_grid.insertRow(row)
        self._index_keys.append(list(keys))
        self.idx_grid.setItem(row, IDX_NAME, QTableWidgetItem(name))
        combo = QComboBox()
        for k in self._kinds(kind):
            combo.addItem(k.value, k)
        combo.setCurrentIndex(combo.findData(kind))
        combo.currentIndexChanged.connect(self._validate)
        self.idx_grid.setCellWidget(row, IDX_KIND, combo)
        columns = QTableWidgetItem()
        columns.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self.idx_grid.setItem(row, IDX_COLUMNS, columns)
        self.idx_grid.blockSignals(False)
        self.idx_grid.setCurrentCell(row, IDX_NAME)
        self._refresh_indexes()
        self._validate()
        return row

    def remove_index(self) -> None:
        row = self.idx_grid.currentRow()
        if row >= 0:
            self._index_keys.pop(row)
            self.idx_grid.removeRow(row)
            self._refresh_indexes()
            self._validate()

    def _refresh_indexes(self, fill_list: bool = True) -> None:
        """Spaltentexte der Indexzeilen und (optional) die Spaltenliste des markierten Index aktualisieren."""
        if not hasattr(self, "idx_grid"):
            return
        names = self._names_by_key()
        self.idx_grid.blockSignals(True)
        for row, keys in enumerate(self._index_keys):
            self.idx_grid.item(row, IDX_COLUMNS).setText(", ".join(names[k] for k in keys if k in names))
        self.idx_grid.blockSignals(False)
        if fill_list:
            self._fill_index_columns()

    def _fill_index_columns(self) -> None:
        """Rechte Liste: zuerst die Indexspalten (angehakt, in Indexreihenfolge), dann die übrigen."""
        row = self.idx_grid.currentRow()
        self.idx_columns.blockSignals(True)
        self.idx_columns.clear()
        if 0 <= row < len(self._index_keys):
            names = self._names_by_key()
            keys = self._index_keys[row]
            for key in [k for k in keys if k in names] + [k for k in names if k not in keys]:
                item = QListWidgetItem(names[key] or "(ohne Namen)")
                item.setData(Qt.ItemDataRole.UserRole, key)
                item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                item.setCheckState(Qt.CheckState.Checked if key in keys else Qt.CheckState.Unchecked)
                self.idx_columns.addItem(item)
        self.idx_columns.setEnabled(row >= 0)
        self.idx_columns.blockSignals(False)

    def _keys_from_list(self) -> list[int]:
        return [
            self.idx_columns.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.idx_columns.count())
            if self.idx_columns.item(i).checkState() == Qt.CheckState.Checked
        ]

    def _index_columns_changed(self, _item: QListWidgetItem | None = None) -> None:
        row = self.idx_grid.currentRow()
        if row < 0:
            return
        # Liste nicht neu aufbauen – sonst springen die Einträge unter dem Mauszeiger
        self._index_keys[row] = self._keys_from_list()
        self._refresh_indexes(fill_list=False)
        self._validate()

    def move_index_column(self, delta: int) -> None:
        current = self.idx_columns.currentRow()
        target = current + delta
        if current < 0 or not 0 <= target < self.idx_columns.count():
            return
        self.idx_columns.blockSignals(True)
        item = self.idx_columns.takeItem(current)
        self.idx_columns.insertItem(target, item)
        self.idx_columns.blockSignals(False)
        self.idx_columns.setCurrentRow(target)
        row = self.idx_grid.currentRow()
        self._index_keys[row] = self._keys_from_list()
        self._refresh_indexes(fill_list=False)
        self._validate()

    def _index_rows(self) -> list[Index]:
        names = self._names_by_key()
        return [
            Index(
                self.idx_grid.item(r, IDX_NAME).text().strip(),
                IndexKind(self.idx_grid.cellWidget(r, IDX_KIND).currentData()),  # Qt liefert str zurück
                tuple(names[k] for k in self._index_keys[r] if k in names),
            )
            for r in range(self.idx_grid.rowCount())
        ]

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
        """Ergebnis: neue Tabelle.

        Indexe nennen die **neuen** Spaltennamen (für `replace_table(..., final_indexes=True)`),
        Fremdschlüssel der alten Tabelle die alten – die zieht `replace_table` über
        `column_renames()` nach.
        """
        rows = self._rows()
        pk_cols = tuple(c.name for c, pk, _ in rows if pk)
        indexes = [Index(self._pk_name(), IndexKind.PRIMARY, pk_cols)] if pk_cols else []
        indexes += self._index_rows()
        old = self._original
        return Table(
            name=self.name.text().strip(),
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
        indexes = self._index_rows()
        index_names = [i.name for i in indexes]
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
        elif any(not n for n in index_names):
            problem = "Jeder Index braucht einen Namen."
        elif len(index_names) != len(set(index_names)) or (
            any(pk for _, pk, _ in rows) and self._pk_name() in index_names
        ):
            problem = "Indexnamen müssen eindeutig sein (auch gegenüber dem Primärschlüssel)."
        elif empty := next((i.name for i in indexes if not i.columns), None):
            problem = f"Index „{empty}“ braucht mindestens eine Spalte."
        self.message.setText(problem)
        self.box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not problem)
