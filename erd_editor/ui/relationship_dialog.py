"""Dialog „Neue Beziehung“: Art wählen, Vorschau der FK-Spalten."""

from __future__ import annotations

from html import escape

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from erd_editor.model import Diagram, TableRef
from erd_editor.model.editing import EditError, RelationshipPlan, plan_relationship


class RelationshipDialog(QDialog):
    def __init__(self, diagram: Diagram, child: TableRef, parent_ref: TableRef, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Neue Beziehung")
        self.setMinimumWidth(420)
        self._diagram, self.child, self.parent_table = diagram, child, parent_ref

        header = QLabel(
            f"Kind <b>{escape(child.table)}</b> → Elternteil <b>{escape(parent_ref.table)}</b><br>"
            "<span style='color:#6B7280'>Die Kindtabelle bekommt den Fremdschlüssel.</span>"
        )
        self.one_to_many = QRadioButton("1:n – ein Elternteil, beliebig viele Kinder")
        self.one_to_one = QRadioButton("1:1 – höchstens ein Kind je Elternteil (UNIQUE)")
        self.one_to_many.setChecked(True)
        kind = QButtonGroup(self)
        kind.addButton(self.one_to_many)
        kind.addButton(self.one_to_one)
        self.identifying = QCheckBox("Identifizierend (schwache Entität: FK wird Teil des Primärschlüssels)")
        self.mandatory = QCheckBox("Jedes Kind braucht ein Elternteil (NOT NULL)")
        self.mandatory.setChecked(True)
        for widget in (self.one_to_many, self.one_to_one, self.identifying, self.mandatory):
            widget.toggled.connect(self._update)

        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.preview.setStyleSheet("background:#F3F4F6; padding:6px; border-radius:4px")
        self.box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.box.button(QDialogButtonBox.StandardButton.Ok).setText("Anlegen")
        self.box.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        self.box.accepted.connect(self.accept)
        self.box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(header)
        layout.addWidget(self.one_to_many)
        layout.addWidget(self.one_to_one)
        layout.addWidget(self.identifying)
        layout.addWidget(self.mandatory)
        layout.addWidget(QLabel("Wird angelegt:"))
        layout.addWidget(self.preview)
        layout.addWidget(self.box)
        self._update()

    def options(self) -> dict:
        return {
            "one_to_one": self.one_to_one.isChecked(),
            "identifying": self.identifying.isChecked(),
            "mandatory": self.mandatory.isChecked() or self.identifying.isChecked(),
        }

    def plan(self) -> RelationshipPlan:
        return plan_relationship(self._diagram, self.child, self.parent_table, **self.options())

    def _update(self, *_args) -> None:
        # identifizierend heißt immer: NOT NULL
        self.mandatory.setEnabled(not self.identifying.isChecked())
        try:
            plan = self.plan()
        except EditError as exc:
            self.preview.setText(f"<span style='color:#B42318'>{escape(str(exc))}</span>")
            self.box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
            return
        lines = []
        for col in plan.columns:
            flags = ["FK"] + (["PK"] if plan.adds_to_primary_key else []) + (["NULL"] if col.nullable else ["NOT NULL"])
            lines.append(f"Spalte <b>{escape(col.name)}</b> {escape(col.data_type)} · {' · '.join(flags)}")
        lines.append(f"Fremdschlüssel <b>{escape(plan.fk_name)}</b> → "
                     f"{escape(self.parent_table.table)} ({escape(', '.join(plan.referenced_columns))})")
        if plan.unique_index:
            lines.append(f"UNIQUE-Index <b>{escape(plan.unique_index)}</b>")
        self.preview.setText("<br>".join(lines))
        self.box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
