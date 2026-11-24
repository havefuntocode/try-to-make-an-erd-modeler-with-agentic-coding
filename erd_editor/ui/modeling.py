"""Modellieren im Hauptfenster: neue Datenbanken und Tabellen, bearbeiten, löschen.

Als Mixin für `MainWindow`. Jede Änderung erzeugt ein neues Diagramm
(siehe `model/editing.py`) und landet als ein Schritt auf dem
Rückgängig-Stapel. Benennt man eine Tabelle um, wandern ihre Positionen,
Teildiagramm-Zugehörigkeit und Überschreibungen mit.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QWidget,
)

from erd_editor.model import Diagram, Dialect, Layout, TableRef
from erd_editor.model.editing import (
    EditError,
    add_database,
    add_relationship,
    delete_relationship,
    add_table,
    delete_table,
    replace_table,
    take_over_as_model,
)
from erd_editor.model.types import DEFAULT_SCHEMAS
from erd_editor.persistence.document import DiagramDocument
from erd_editor.ui.relationship_dialog import RelationshipDialog
from erd_editor.ui.table_dialog import TableDialog
from erd_editor.ui.toolbar import app_settings
from erd_editor.ui.view_dialog import ViewDialog

READ_ONLY_HINT = (
    "Die Datenbank „{name}“ wurde aus einer Datenbank eingelesen "
    "und kann nicht verändert werden."
)


class DatabaseDialog(QDialog):
    """Neue Modell-Datenbank: Name, Dialekt, Schema."""

    def __init__(self, taken: set[str], parent: QWidget | None = None, title: str = "Neue Datenbank (Modell)",
                 intro: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(400)
        self._taken = taken
        self.name = QLineEdit()
        self.name.setPlaceholderText("z. B. shop")
        self.dialect = QComboBox()
        for dialect in Dialect:
            self.dialect.addItem(dialect.label, dialect.value)
        self.schema = QLineEdit()
        self.message = QLabel()
        self.message.setStyleSheet("color:#B42318")
        self.box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.box.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        self.box.accepted.connect(self.accept)
        self.box.rejected.connect(self.reject)
        form = QFormLayout(self)
        if intro:
            label = QLabel(intro)
            label.setWordWrap(True)
            form.addRow(label)
        form.addRow("Name:", self.name)
        form.addRow("Dialekt:", self.dialect)
        form.addRow("Schema:", self.schema)
        form.addRow(self.message)
        form.addRow(self.box)
        self.name.textChanged.connect(self._validate)
        self.dialect.currentIndexChanged.connect(self._dialect_changed)
        # zuletzt gewählter Dialekt als Vorbelegung
        last = self.dialect.findData(str(app_settings().value("model/dialect", Dialect.MYSQL.value)))
        if last >= 0:
            self.dialect.setCurrentIndex(last)
        self._dialect_changed()

    def accept(self) -> None:
        app_settings().setValue("model/dialect", self.chosen_dialect().value)
        super().accept()

    def chosen_dialect(self) -> Dialect:
        return Dialect(self.dialect.currentData())

    def _dialect_changed(self, *_args) -> None:
        default = DEFAULT_SCHEMAS[self.chosen_dialect()]
        self.schema.setEnabled(default is not None)
        self.schema.setText(default or "")
        self.schema.setPlaceholderText("" if default else "MySQL hat keine Schemaebene")
        self._validate()

    def _validate(self, *_args) -> None:
        name = self.name.text().strip()
        problem = "Bitte einen Namen eingeben." if not name else (
            f"Eine Datenbank „{name}“ gibt es schon." if name in self._taken else "")
        self.message.setText(problem)
        self.box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not problem)


class ModelingMixin:
    # ------------------------------------------------------------------ Datei → Neu

    def new_document(self) -> None:
        """Neues Diagramm mit einer ersten Modell-Datenbank (Name und Dialekt im Dialog).

        Abbrechen bricht „Neu“ ganz ab – das bisherige Diagramm bleibt offen,
        ein leeres Diagramm ohne Datenbank entsteht nicht.
        """
        if not self._confirm_discard():
            return
        dialog = DatabaseDialog(set(), self, "Neues Diagramm",
                                "Das Diagramm beginnt mit einer Modell-Datenbank. Name und Dialekt "
                                "bestimmen Datentypen und SQL-Export.")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.statusBar().showMessage("Neues Diagramm abgebrochen")
            return
        self.path = None
        self.show_document(DiagramDocument(), use_layout=False)
        self.create_database(dialog.name.text().strip(), dialog.chosen_dialect(), dialog.schema.text().strip() or None)
        self.undo_stack.clear()  # die erste Datenbank gehört zum neuen Diagramm, nicht rückgängig zu machen
        self._update_title()
        self.setWindowModified(False)
        self.statusBar().showMessage("Neues Diagramm – Modell → Neue Tabelle … (Strg+Umschalt+N)")

    # ------------------------------------------------------------------ Änderungen anwenden

    def apply_model_change(self, diagram: Diagram, text: str, renamed: tuple[TableRef, TableRef] | None = None,
                           added: TableRef | None = None) -> None:
        """Neues Diagramm übernehmen und als ein Rückgängig-Schritt ablegen."""
        before = self.snapshot()
        self.document.diagram = diagram
        if renamed is not None:
            self._move_ref(*renamed)
        if added is not None and self.current_view is not None:
            self.document.subdiagram(self.current_view).tables.append(added)  # neue Tabelle auch im Teildiagramm
        self.prune_subdiagrams(diagram)
        self.show_view(self.current_view, keep_view=True, renamed=dict([renamed]) if renamed else None)
        self.record_document(before, text)
        self.statusBar().showMessage(text)

    def _move_ref(self, old: TableRef, new: TableRef) -> None:
        """Umbenannte Tabelle: Positionen, Teildiagramme und Überschreibungen mitnehmen."""
        if old == new:
            return

        def moved(layout: Layout | None) -> Layout | None:
            if layout is None:
                return layout
            positions = {(new if r == old else r): p for r, p in layout.positions.items()}
            notes = [replace(n, entity=new) if n.entity == old else n for n in layout.notes]  # Notizen bleiben verbunden
            return replace(layout, positions=positions, notes=notes)

        self.document.layout = moved(self.document.layout)
        for sd in self.document.subdiagrams:
            sd.layout = moved(sd.layout)
            sd.tables = [new if r == old else r for r in sd.tables]
        self.document.overrides = {
            ((new if child == old else child), fk): o for (child, fk), o in self.document.overrides.items()
        }

    def _try(self, action) -> Diagram | None:
        try:
            return action()
        except EditError as exc:
            QMessageBox.warning(self, "Nicht möglich", str(exc))
            return None

    def _read_only_notice(self, database: str) -> None:
        """Hinweisfenster: eingelesene Datenbanken sind schreibgeschützt."""
        QMessageBox.information(self, "Schreibgeschützt", READ_ONLY_HINT.format(name=database))

    # ------------------------------------------------------------------ Datenbanken

    def new_database_dialog(self) -> bool:
        """Modell → Neue Datenbank …; True, wenn eine angelegt wurde."""
        dialog = DatabaseDialog({db.name for db in self.document.diagram.databases}, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        before = len(self.document.diagram.databases)
        self.create_database(dialog.name.text().strip(), dialog.chosen_dialect(), dialog.schema.text().strip() or None)
        return len(self.document.diagram.databases) > before

    def offer_new_database(self, purpose: str) -> bool:
        """Keine Modell-Datenbank da: fragen, ob gleich eine angelegt werden soll."""
        reason = " – eingelesene Datenbanken sind schreibgeschützt" if self.document.diagram.databases else ""
        answer = QMessageBox.question(
            self, "Keine Modell-Datenbank",
            f"{purpose} braucht eine Modell-Datenbank{reason}.\n\nJetzt eine neue Datenbank anlegen?")
        return answer == QMessageBox.StandardButton.Yes and self.new_database_dialog()

    def create_database(self, name: str, dialect: Dialect, schema: str | None = None) -> None:
        diagram = self._try(lambda: add_database(self.document.diagram, name, dialect, schema))
        if diagram is not None:
            self.apply_model_change(diagram, f"Datenbank „{name}“ anlegen")

    def take_over_dialog(self) -> None:
        names = [db.name for db in self.document.diagram.databases if not db.modeled]
        if not names:
            self.statusBar().showMessage("Alle Datenbanken sind bereits Modelle")
            return
        name, ok = QInputDialog.getItem(self, "Als Modell übernehmen",
                                        "Datenbank (wird bearbeitbar; Neu einlesen entfällt für sie):", names, 0, False)
        if ok:
            self.take_over(name)

    def take_over(self, name: str) -> None:
        diagram = self._try(lambda: take_over_as_model(self.document.diagram, name))
        if diagram is not None:
            self.apply_model_change(diagram, f"„{name}“ als Modell übernehmen")

    # ------------------------------------------------------------------ Tabellen

    def editable_targets(self) -> list[tuple[str, str | None]]:
        """(Datenbank, Schema) aller bearbeitbaren Orte."""
        return [(db.name, s.name) for db in self.document.diagram.databases if db.modeled for s in db.schemas]

    def _choose_target(self, purpose: str = "Eine neue Tabelle") -> tuple[str, str | None] | None:
        targets = self.editable_targets()
        if not targets:
            if not self.offer_new_database(purpose):
                return None
            targets = self.editable_targets()
            if not targets:
                return None
        # bevorzugt die Datenbank der markierten Tabelle
        for ref in self.selected_refs():
            if (ref.database, ref.schema) in targets:
                return ref.database, ref.schema
        if len(targets) == 1:
            return targets[0]
        labels = [f"{db}.{schema}" if schema else db for db, schema in targets]
        label, ok = QInputDialog.getItem(self, "Neue Tabelle", "In welche Datenbank?", labels, 0, False)
        return targets[labels.index(label)] if ok else None

    def new_table_dialog(self) -> None:
        target = self._choose_target()
        if target is None:
            return
        db_name, schema = target
        db = self.document.diagram.database(db_name)
        taken = {t.name for t in db.schema(schema).tables}
        location = f"{db_name}.{schema}" if schema else db_name
        dialog = TableDialog(db.dialect, None, taken, location, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.create_table(TableRef(db_name, schema, dialog.table().name), dialog.table())

    def create_table(self, ref: TableRef, table) -> None:
        diagram = self._try(lambda: add_table(self.document.diagram, ref, table))
        if diagram is not None:
            noun = "View" if table.is_view else "Tabelle"
            self.apply_model_change(diagram, f"{noun} „{ref.table}“ anlegen", added=ref)

    # ------------------------------------------------------------------ Views

    def _view_candidates(self, database: str) -> list[TableRef]:
        """Objekte, die eine View in dieser Datenbank benutzen kann."""
        return [ref for ref, _ in self.document.diagram.tables() if ref.database == database]

    def new_view_dialog(self) -> None:
        """Modell → Neue View …"""
        target = self._choose_target("Eine neue View")
        if target is None:
            return
        db_name, schema = target
        db = self.document.diagram.database(db_name)
        taken = {t.name for t in db.schema(schema).tables}
        location = f"{db_name}.{schema}" if schema else db_name
        dialog = ViewDialog(db.dialect, None, taken, location, self._view_candidates(db_name), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            view = dialog.view()
            self.create_table(TableRef(db_name, schema, view.name), view)

    def edit_table(self, ref: TableRef) -> None:
        """Doppelklick auf eine Box."""
        db = self.document.diagram.database(ref.database)
        table = self.document.diagram.find_table(ref)
        if db is None or table is None:
            return
        if not db.modeled:
            self._read_only_notice(ref.database)
            return
        taken = {t.name for t in db.schema(ref.schema).tables}
        location = f"{ref.database}.{ref.schema}" if ref.schema else ref.database
        if table.is_view:
            dialog = ViewDialog(db.dialect, table, taken, location, self._view_candidates(ref.database), self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.update_table(ref, dialog.view(), {}, final_indexes=True)
            return
        dialog = TableDialog(db.dialect, table, taken, location, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.update_table(ref, dialog.table(), dialog.column_renames(), final_indexes=True)

    def update_table(self, ref: TableRef, table, renames: dict[str, str], final_indexes: bool = False) -> None:
        diagram = self._try(lambda: replace_table(self.document.diagram, ref, table, renames, final_indexes))
        if diagram is not None:
            new_ref = TableRef(ref.database, ref.schema, table.name)
            noun = "View" if table.is_view else "Tabelle"
            self.apply_model_change(diagram, f"{noun} „{table.name}“ bearbeiten", renamed=(ref, new_ref))

    def delete_selected_tables(self) -> None:
        refs = [r for r in self.selected_refs() if self.document.diagram.database(r.database).modeled]
        if not refs:
            self.statusBar().showMessage("Bitte Tabellen einer Modell-Datenbank markieren")
            return
        names = ", ".join(r.table for r in refs)
        answer = QMessageBox.question(self, "Tabellen löschen",
                                      f"Löschen: {names}?\nFremdschlüssel, die darauf zeigen, entfallen ebenfalls.")
        if answer == QMessageBox.StandardButton.Yes:
            self.delete_tables(refs)

    def delete_tables(self, refs: list[TableRef]) -> None:
        diagram = self.document.diagram
        for ref in refs:
            diagram = self._try(lambda r=ref, d=diagram: delete_table(d, r))
            if diagram is None:
                return
        text = f"Tabelle „{refs[0].table}“ löschen" if len(refs) == 1 else f"{len(refs)} Tabellen löschen"
        self.apply_model_change(diagram, text)

    # ------------------------------------------------------------------ Beziehungen

    def start_relation_tool(self) -> None:
        """Modell → Beziehung zeichnen: von der Kind- zur Elterntabelle ziehen."""
        if not self.editable_targets():
            # Eine neue Datenbank ist leer – Beziehungen brauchen erst Tabellen
            if self.offer_new_database("Eine Beziehung"):
                self.statusBar().showMessage("Datenbank angelegt – jetzt Tabellen anlegen (Strg+Umschalt+N), "
                                             "dann Beziehungen zeichnen")
            return
        self.view.set_relation_mode(True)
        self.statusBar().showMessage("Beziehung zeichnen: auf die Kindtabelle drücken und zur Elterntabelle ziehen (Esc bricht ab)")

    def on_relation_drawn(self, child: TableRef, parent: TableRef) -> None:
        if not self.document.diagram.database(child.database).modeled:
            self._read_only_notice(child.database)
            return
        dialog = RelationshipDialog(self.document.diagram, child, parent, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.create_relationship(child, parent, **dialog.options())

    def create_relationship(self, child: TableRef, parent: TableRef, one_to_one: bool = False,
                            identifying: bool = False, mandatory: bool = True) -> None:
        result = self._try(lambda: add_relationship(self.document.diagram, child, parent,
                                                    one_to_one, identifying, mandatory))
        if result is not None:
            diagram, plan = result
            self.apply_model_change(diagram, f"Beziehung „{plan.fk_name}“ anlegen")

    def remove_relationship(self, child: TableRef, fk_name: str, drop_columns: bool = True) -> None:
        diagram = self._try(lambda: delete_relationship(self.document.diagram, child, fk_name, drop_columns))
        if diagram is not None:
            self.apply_model_change(diagram, f"Beziehung „{fk_name}“ löschen")

    # ------------------------------------------------------------------ SQL-Export

    def sql_export_dialog(self) -> None:
        """Modell → SQL-Skript erzeugen … (vorausgewählt: Modell-Datenbanken, sonst alle)."""
        from erd_editor.ui.sql_dialog import SqlExportDialog

        diagram = self.document.diagram
        if not diagram.databases:
            self.statusBar().showMessage("Nichts zu exportieren – zuerst eine Datenbank anlegen oder einlesen")
            return
        modeled = [db.name for db in diagram.databases if db.modeled]
        start = self.path.parent if self.path else None
        SqlExportDialog(diagram, modeled or None, start, self).exec()
