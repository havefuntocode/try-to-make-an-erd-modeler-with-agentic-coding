"""View-Editor: Name, Kommentar, SQL-Text, Spalten und benutzte Objekte einer Modell-View."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
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
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from erd_editor.analysis.view_sql import guess_dependencies
from erd_editor.model import Column, Dialect, Table, TableKind, TableRef
from erd_editor.model.types import COMMON_TYPES

REF = Qt.ItemDataRole.UserRole


class ViewDialog(QDialog):
    """Neue View anlegen oder vorhandene bearbeiten.

    `candidates` sind die Objekte, die die View benutzen kann (Tabellen und
    Views derselben Datenbank, ohne sie selbst). Solange man „Benutzt“ nicht
    von Hand ändert, folgt die Auswahl dem SQL-Text.
    """

    def __init__(
        self,
        dialect: Dialect,
        view: Table | None = None,
        taken_names: set[str] | None = None,
        location: str = "",
        candidates: list[TableRef] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.dialect = dialect
        self._original = view
        self._taken = set(taken_names or ()) - ({view.name} if view else set())
        self._candidates = [c for c in (candidates or []) if view is None or c.table != view.name]
        self._manual_deps = view is not None  # beim Bearbeiten gilt die gespeicherte Auswahl
        self.setWindowTitle(("View bearbeiten" if view else "Neue View") + (f" – {location}" if location else ""))
        self.resize(720, 560)

        self.name = QLineEdit(view.name if view else "")
        self.name.setPlaceholderText("z. B. v_kunde_umsatz")
        self.comment = QLineEdit((view.comment or "") if view else "")
        self.sql = QPlainTextEdit((view.definition or "") if view else "")
        self.sql.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.sql.setPlaceholderText("SELECT …  (nur der SELECT-Teil, ohne CREATE VIEW)")
        self.sql.textChanged.connect(self._sql_changed)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_sql_page(), "SQL")
        self.tabs.addTab(self._build_columns_page(view), "Spalten")
        self.tabs.addTab(self._build_deps_page(view), "Benutzt")

        self.message = QLabel()
        self.message.setStyleSheet("color:#B42318")
        self.box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.box.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        self.box.accepted.connect(self.accept)
        self.box.rejected.connect(self.reject)

        form = QFormLayout()
        form.addRow("Name:", self.name)
        form.addRow("Kommentar:", self.comment)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.message)
        layout.addWidget(self.box)
        self.name.textChanged.connect(self._validate)
        self._validate()

    # ------------------------------------------------------------------ Seiten

    def _build_sql_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.addWidget(self.sql)
        hint = QLabel("Der SQL-Text wird beim Export unverändert übernommen – er muss zum Dialekt passen.")
        hint.setStyleSheet("color:#6B7280")
        v.addWidget(hint)
        return page

    def _build_columns_page(self, view: Table | None) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        self.columns = QTableWidget(0, 2)
        self.columns.setHorizontalHeaderLabels(["Name", "Datentyp"])
        self.columns.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.columns.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.columns.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        for column in view.columns if view else []:
            self.add_column(column)
        buttons = QHBoxLayout()
        add = QPushButton("Spalte hinzufügen")
        add.clicked.connect(lambda: self.add_column(Column("", COMMON_TYPES[self.dialect][0])))
        remove = QPushButton("Entfernen")
        remove.clicked.connect(self.remove_selected_columns)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        v.addWidget(QLabel("Spalten der View (für die Anzeige im Diagramm):"))
        v.addWidget(self.columns, 1)
        v.addLayout(buttons)
        return page

    def _build_deps_page(self, view: Table | None) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        self.deps = QListWidget()
        chosen = set(view.view_dependencies) if view else set()
        for ref in self._candidates:
            label = f"{ref.schema}.{ref.table}" if ref.schema else ref.table
            item = QListWidgetItem(label)
            item.setData(REF, ref)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if ref in chosen else Qt.CheckState.Unchecked)
            self.deps.addItem(item)
        self.deps.itemChanged.connect(self._deps_edited)
        detect = QPushButton("Aus SQL erkennen")
        detect.clicked.connect(self.detect_dependencies)
        v.addWidget(QLabel("Tabellen und Views, die diese View benutzt (gepunktete Linien im Diagramm):"))
        v.addWidget(self.deps, 1)
        row = QHBoxLayout()
        row.addWidget(detect)
        row.addStretch(1)
        v.addLayout(row)
        return page

    # ------------------------------------------------------------------ Spalten

    def add_column(self, column: Column) -> int:
        row = self.columns.rowCount()
        self.columns.insertRow(row)
        self.columns.setItem(row, 0, QTableWidgetItem(column.name))
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(COMMON_TYPES[self.dialect])
        combo.setCurrentText(column.data_type)
        self.columns.setCellWidget(row, 1, combo)
        return row

    def remove_selected_columns(self) -> None:
        for row in sorted({i.row() for i in self.columns.selectedIndexes()}, reverse=True):
            self.columns.removeRow(row)

    def _columns(self) -> list[Column]:
        result = []
        for row in range(self.columns.rowCount()):
            item = self.columns.item(row, 0)
            name = item.text().strip() if item else ""
            if name:
                result.append(Column(name, self.columns.cellWidget(row, 1).currentText().strip() or "?"))
        return result

    # ------------------------------------------------------------------ Benutzt

    def _set_dependencies(self, refs: list[TableRef]) -> None:
        wanted = set(refs)
        self.deps.blockSignals(True)
        try:
            for i in range(self.deps.count()):
                item = self.deps.item(i)
                item.setCheckState(Qt.CheckState.Checked if item.data(REF) in wanted else Qt.CheckState.Unchecked)
        finally:
            self.deps.blockSignals(False)

    def detect_dependencies(self) -> None:
        """Auswahl aus dem SQL-Text neu bestimmen (und ab jetzt wieder automatisch folgen)."""
        self._manual_deps = False
        self._set_dependencies(guess_dependencies(self.sql.toPlainText(), self._candidates))

    def _sql_changed(self) -> None:
        if not self._manual_deps:
            self._set_dependencies(guess_dependencies(self.sql.toPlainText(), self._candidates))

    def _deps_edited(self, _item) -> None:
        self._manual_deps = True

    def dependencies(self) -> list[TableRef]:
        return [self.deps.item(i).data(REF) for i in range(self.deps.count())
                if self.deps.item(i).checkState() == Qt.CheckState.Checked]

    # ------------------------------------------------------------------ Ergebnis

    def _validate(self, *_args) -> None:
        name = self.name.text().strip()
        problem = "Bitte einen Namen eingeben." if not name else (
            f"„{name}“ gibt es dort schon." if name in self._taken else "")
        self.message.setText(problem)
        self.box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not problem)

    def accept(self) -> None:
        names = [c.name for c in self._columns()]
        if len(names) != len(set(names)):
            self.message.setText("Spaltennamen müssen eindeutig sein.")
            self.tabs.setCurrentIndex(1)
            return
        super().accept()

    def view(self) -> Table:
        """Die View, wie sie im Dialog steht."""
        return Table(
            self.name.text().strip(),
            self._columns(),
            comment=self.comment.text().strip() or None,
            kind=TableKind.VIEW,
            definition=self.sql.toPlainText().strip() or None,
            view_dependencies=self.dependencies(),
        )
