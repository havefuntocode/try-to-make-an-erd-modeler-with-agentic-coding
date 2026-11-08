"""Dialog „Teildiagramm“: Name und Tabellen auswählen."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
)

from erd_editor.model import Diagram, TableRef
from erd_editor.model.subdiagram import neighbours

REF_ROLE = Qt.ItemDataRole.UserRole


class SubdiagramDialog(QDialog):
    """Name eingeben und Tabellen ankreuzen (gruppiert nach Datenbank und Schema)."""

    def __init__(
        self,
        diagram: Diagram,
        name: str = "",
        selected: list[TableRef] | None = None,
        taken_names: set[str] | None = None,
        title: str = "Neues Teildiagramm",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(460, 560)
        self._diagram = diagram
        self._taken = set(taken_names or set()) - {name}

        self.name = QLineEdit(name)
        self.name.setPlaceholderText("z. B. Bestellwesen")
        self.name.textChanged.connect(self._update_ok)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Tabellen filtern …")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._apply_filter)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self._items: dict[TableRef, QTreeWidgetItem] = {}
        self._fill(set(selected or []))
        self.tree.itemChanged.connect(self._update_ok)

        neighbours_button = QPushButton("Nachbarn ergänzen")
        neighbours_button.setToolTip("Direkt über Fremdschlüssel verbundene Tabellen hinzufügen")
        neighbours_button.clicked.connect(self.add_neighbours)
        all_button = QPushButton("Alle")
        all_button.clicked.connect(lambda: self._set_all(True))
        none_button = QPushButton("Keine")
        none_button.clicked.connect(lambda: self._set_all(False))
        self.count = QLabel()
        buttons_row = QHBoxLayout()
        for b in (neighbours_button, all_button, none_button):
            buttons_row.addWidget(b)
        buttons_row.addStretch(1)
        buttons_row.addWidget(self.count)

        self.message = QLabel()
        self.message.setStyleSheet("color:#B42318")
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        form = QFormLayout()
        form.addRow("Name:", self.name)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.filter)
        layout.addWidget(self.tree, 1)
        layout.addLayout(buttons_row)
        layout.addWidget(self.message)
        layout.addWidget(self.buttons)
        self._update_ok()

    # ------------------------------------------------------------------ Baum

    def _fill(self, selected: set[TableRef]) -> None:
        flags = Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
        tristate = flags | Qt.ItemFlag.ItemIsAutoTristate
        for db in self._diagram.databases:
            db_item = QTreeWidgetItem(self.tree, [db.name])
            db_item.setFlags(tristate)
            for schema in db.schemas:
                parent = db_item
                if schema.name is not None:
                    parent = QTreeWidgetItem(db_item, [schema.name])
                    parent.setFlags(tristate)
                for table in schema.tables:
                    ref = TableRef(db.name, schema.name, table.name)
                    item = QTreeWidgetItem(parent, [table.name])
                    item.setFlags(flags)
                    item.setData(0, REF_ROLE, ref)
                    item.setCheckState(0, Qt.CheckState.Checked if ref in selected else Qt.CheckState.Unchecked)
                    self._items[ref] = item
        self.tree.expandAll()

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for ref, item in self._items.items():
            item.setHidden(bool(needle) and needle not in ref.table.lower())
        # Gruppen ohne sichtbare Tabellen ausblenden
        it = QTreeWidgetItemIterator(self.tree)
        groups = []
        while it.value():
            if it.value().data(0, REF_ROLE) is None:
                groups.append(it.value())
            it += 1
        for group in reversed(groups):  # Schemas vor Datenbanken
            visible = any(not group.child(i).isHidden() for i in range(group.childCount()))
            group.setHidden(not visible)

    def _set_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for item in self._items.values():
            if not item.isHidden():
                item.setCheckState(0, state)

    def add_neighbours(self) -> None:
        for ref in neighbours(self._diagram, set(self.selected_tables())):
            self._items[ref].setCheckState(0, Qt.CheckState.Checked)

    # ------------------------------------------------------------------ Ergebnis

    def selected_tables(self) -> list[TableRef]:
        return [ref for ref, item in self._items.items() if item.checkState(0) == Qt.CheckState.Checked]

    def chosen_name(self) -> str:
        return self.name.text().strip()

    def _update_ok(self, *_args) -> None:
        tables = self.selected_tables()
        self.count.setText(f"{len(tables)} von {len(self._items)} Tabellen")
        name = self.chosen_name()
        problem = ""
        if not name:
            problem = "Bitte einen Namen eingeben."
        elif name in self._taken or name == "Gesamt":
            problem = f"Den Namen „{name}“ gibt es schon."
        elif not tables:
            problem = "Bitte mindestens eine Tabelle ankreuzen."
        self.message.setText(problem)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not problem)
