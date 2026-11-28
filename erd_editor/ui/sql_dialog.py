"""Dialog „SQL-Skript erzeugen“: Datenbanken wählen, Optionen, Vorschau, kopieren/speichern."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from erd_editor.export.ddl import DIALECT_NAMES, DdlOptions, generate_script
from erd_editor.model import Diagram
from erd_editor.ui import recent


class SqlExportDialog(QDialog):
    def __init__(self, diagram: Diagram, preselect: list[str] | None = None, start_dir: Path | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("SQL-Skript erzeugen")
        self.resize(980, 640)
        self._diagram = diagram
        self._start_dir = start_dir or Path.cwd()
        wanted = set(preselect or [db.name for db in diagram.databases])

        self.databases = QListWidget()
        for db in diagram.databases:
            label = f"{db.name}  ({DIALECT_NAMES[db.dialect]}{', Modell' if db.modeled else ''})"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, db.name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if db.name in wanted else Qt.CheckState.Unchecked)
            self.databases.addItem(item)
        self.databases.itemChanged.connect(self.refresh)

        self.comments = QCheckBox("Kommentare übernehmen")
        self.comments.setChecked(True)
        self.comments.toggled.connect(self.refresh)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.preview.setFont(font)

        copy_button = QPushButton("In Zwischenablage kopieren")
        copy_button.clicked.connect(self.copy_to_clipboard)
        save_button = QPushButton("Speichern …")
        save_button.clicked.connect(self.save_dialog)
        close_button = QPushButton("Schließen")
        close_button.clicked.connect(self.accept)

        left = QVBoxLayout()
        left.addWidget(QLabel("Datenbanken:"))
        left.addWidget(self.databases, 1)
        left.addWidget(self.comments)
        safety = QLabel("Das Skript legt jede Datenbank <b>neu</b> an. Gibt es sie schon, bricht es ab – "
                        "bestehende Datenbanken werden nie verändert, es enthält kein DROP.")
        safety.setWordWrap(True)
        left.addWidget(safety)
        hint = QLabel("Fremdschlüssel stehen am Ende des Skripts – die Reihenfolge der Tabellen spielt keine Rolle.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#6B7280")
        left.addWidget(hint)
        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setMaximumWidth(320)

        body = QHBoxLayout()
        body.addWidget(left_widget)
        body.addWidget(self.preview, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        for b in (copy_button, save_button, close_button):
            buttons.addWidget(b)
        layout = QVBoxLayout(self)
        layout.addLayout(body, 1)
        layout.addLayout(buttons)
        self.refresh()

    # ------------------------------------------------------------------ Inhalt

    def selected(self) -> list[str]:
        return [
            self.databases.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.databases.count())
            if self.databases.item(i).checkState() == Qt.CheckState.Checked
        ]

    def options(self) -> DdlOptions:
        return DdlOptions(comments=self.comments.isChecked())

    def script(self) -> str:
        names = self.selected()
        if not names:
            return "-- Bitte mindestens eine Datenbank ankreuzen."
        return generate_script(self._diagram, names, self.options())

    def refresh(self, *_args) -> None:
        self.preview.setPlainText(self.script())

    # ------------------------------------------------------------------ Aktionen

    def copy_to_clipboard(self) -> None:
        QGuiApplication.clipboard().setText(self.script())

    def suggested_path(self) -> Path:
        names = self.selected()
        stem = names[0] if len(names) == 1 else "schema"
        return (recent.last_dir(recent.SQL) or self._start_dir) / f"{stem}.sql"

    def save_dialog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "SQL-Skript speichern", str(self.suggested_path()), "SQL (*.sql)")
        if path:
            self.save(path if path.lower().endswith(".sql") else path + ".sql")

    def save(self, path: str) -> None:
        try:
            Path(path).write_text(self.script(), encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Speichern fehlgeschlagen", str(exc))
            return
        recent.remember_dir(recent.SQL, path)
