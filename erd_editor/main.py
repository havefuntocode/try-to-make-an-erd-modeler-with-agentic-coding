"""Einstiegspunkt des ER-Diagramm-Editors.

Vorläufiger Aufruf, bis es einen Verbindungsdialog gibt:

    python -m erd_editor.main --mysql root@localhost erd_test_shop erd_test_stammdaten
"""

from __future__ import annotations

import argparse
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QInputDialog,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QStyleFactory,
)

from erd_editor.adapters.base import AdapterError
from erd_editor.adapters.mysql import MySqlAdapter
from erd_editor.diagram.scene import DiagramScene
from erd_editor.diagram.view import DiagramView
from erd_editor.model import ConnectionProfile, Diagram, Dialect
from erd_editor.persistence import credentials

USAGE_HINT = "Aufruf: python -m erd_editor.main --mysql root@localhost erd_test_shop erd_test_stammdaten"


def parse_target(target: str) -> tuple[str, str, int | None]:
    """`user@host[:port]` zerlegen."""
    if "@" not in target:
        raise ValueError(f"Erwartet user@host[:port], erhalten: {target!r}")
    user, _, host_port = target.rpartition("@")
    host, _, port = host_port.partition(":")
    if not user or not host:
        raise ValueError(f"Erwartet user@host[:port], erhalten: {target!r}")
    return user, host, int(port) if port else None


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ER-Diagramm-Editor")
        self.resize(1200, 800)
        self.scene = DiagramScene(self)
        self.view = DiagramView(self.scene, self)
        self.setCentralWidget(self.view)
        self._create_menus()
        self.statusBar().showMessage(USAGE_HINT)

    def _create_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&Datei")
        quit_action = QAction("&Beenden", self, shortcut=QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        layout_menu = self.menuBar().addMenu("A&nordnen")
        auto_action = QAction("&Automatisch anordnen", self, shortcut=QKeySequence("Ctrl+L"))
        auto_action.triggered.connect(self._auto_layout)
        layout_menu.addAction(auto_action)

        view_menu = self.menuBar().addMenu("&Ansicht")
        for text, keys, slot in [
            ("Vergrößern", [QKeySequence.StandardKey.ZoomIn, QKeySequence("Ctrl+=")], self.view.zoom_in),
            ("Verkleinern", [QKeySequence.StandardKey.ZoomOut], self.view.zoom_out),
            ("Ganzes Diagramm", [QKeySequence("Ctrl+0")], self.view.zoom_to_fit),
            ("100 %", [QKeySequence("Ctrl+1")], lambda: self.view.set_zoom(1.0)),
        ]:
            action = QAction(text, self)
            action.setShortcuts(keys)
            action.triggered.connect(slot)
            view_menu.addAction(action)

    def _auto_layout(self) -> None:
        self.scene.auto_layout()
        self.view.zoom_to_fit()

    def load_diagram(self, diagram: Diagram) -> None:
        self.scene.load(diagram)
        problems = diagram.validate()
        msg = f"{len(self.scene.entities)} Tabellen aus {len(diagram.databases)} Datenbank(en) eingelesen"
        if problems:
            msg += f" – {len(problems)} Hinweis(e), z. B.: {problems[0]}"
        self.statusBar().showMessage(msg)
        QTimer.singleShot(0, self.view.zoom_to_fit)

    def load_mysql(self, target: str, databases: list[str]) -> None:
        try:
            user, host, port = parse_target(target)
        except ValueError as exc:
            QMessageBox.critical(self, "Ungültige Verbindung", str(exc))
            return
        profile = ConnectionProfile(f"{user}@{host}", Dialect.MYSQL, host, port, user)

        password = credentials.get_password(profile)
        newly_entered = password is None
        if password is None:
            password, ok = QInputDialog.getText(
                self, "Passwort", f"Passwort für {user}@{host}:", QLineEdit.EchoMode.Password
            )
            if not ok:
                return

        try:
            with MySqlAdapter(profile, password) as adapter:
                diagram = Diagram([adapter.read_database(name) for name in databases])
        except AdapterError as exc:
            QMessageBox.critical(self, "Fehler beim Einlesen", str(exc))
            return

        if newly_entered:
            answer = QMessageBox.question(
                self,
                "Passwort speichern?",
                "Passwort in der Windows-Anmeldeinformationsverwaltung speichern?",
            )
            if answer == QMessageBox.StandardButton.Yes:
                credentials.set_password(profile, password)

        self.load_diagram(diagram)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    parser = argparse.ArgumentParser(prog="erd_editor")
    parser.add_argument("--mysql", metavar="USER@HOST[:PORT]", help="MySQL-Server, von dem gelesen wird")
    parser.add_argument("databases", nargs="*", help="einzulesende Datenbanken")
    args = parser.parse_args(argv[1:])

    app = QApplication(argv)
    if "windows11" in [k.lower() for k in QStyleFactory.keys()]:
        app.setStyle("windows11")
    else:
        app.setStyle("Fusion")

    window = MainWindow()
    window.show()
    if args.mysql:
        if not args.databases:
            QMessageBox.critical(window, "Keine Datenbank", "Bitte mindestens eine Datenbank angeben.\n" + USAGE_HINT)
        else:
            window.load_mysql(args.mysql, args.databases)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
