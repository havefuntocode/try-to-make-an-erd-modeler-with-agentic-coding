"""Einstiegspunkt des ER-Diagramm-Editors."""

import sys

from PySide6.QtWidgets import QApplication, QMainWindow


def main() -> int:
    app = QApplication(sys.argv)
    window = QMainWindow()
    window.setWindowTitle("ER-Diagramm-Editor")
    window.resize(1200, 800)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
