"""Dialog „Datenbank einlesen“: Verbindung wählen/anlegen, Datenbanken ankreuzen."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from erd_editor.adapters.base import AdapterError, SchemaAdapter
from erd_editor.adapters.mysql import MySqlAdapter
from erd_editor.adapters.postgres import PostgresAdapter
from erd_editor.model import ConnectionProfile, Dialect
from erd_editor.persistence import credentials

AdapterFactory = Callable[[ConnectionProfile, str | None], SchemaAdapter]

DIALECTS = [
    (Dialect.MYSQL, "MySQL", 3306, True),
    (Dialect.POSTGRESQL, "PostgreSQL", 5432, True),
    (Dialect.MSSQL, "SQL Server (ab Version 2)", 1433, False),
]


DEFAULT_USERS = {Dialect.MYSQL: "root", Dialect.POSTGRESQL: "postgres", Dialect.MSSQL: "sa"}


def default_adapter_factory(profile: ConnectionProfile, password: str | None) -> SchemaAdapter:
    if profile.dialect is Dialect.MYSQL:
        return MySqlAdapter(profile, password)
    if profile.dialect is Dialect.POSTGRESQL:
        return PostgresAdapter(profile, password)
    raise AdapterError(f"Dialekt {profile.dialect.value} folgt in Version 2")


@dataclass
class ConnectionChoice:
    """Ergebnis des Dialogs."""

    profile: ConnectionProfile
    password: str
    save_password: bool
    databases: list[str]
    add_to_diagram: bool


class ConnectionDialog(QDialog):
    def __init__(
        self,
        profiles: list[ConnectionProfile] | None = None,
        can_add: bool = False,
        adapter_factory: AdapterFactory = default_adapter_factory,
        preselect: list[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Datenbank einlesen")
        self.setMinimumWidth(460)
        self._profiles = list(profiles or [])
        self._adapter_factory = adapter_factory
        self._preselect = set(preselect or [])

        # Gespeicherte Verbindungen
        self.saved = QComboBox()
        self.saved.addItem("Neue Verbindung")
        for p in self._profiles:
            self.saved.addItem(p.name)
        self.saved.currentIndexChanged.connect(self._fill_from_saved)

        # Verbindungsdaten
        self.name = QLineEdit()
        self.name.setPlaceholderText("z. B. root@localhost (wird automatisch vergeben)")
        self.dialect = QComboBox()
        for dialect, label, _, enabled in DIALECTS:
            self.dialect.addItem(label, dialect.value)
            if not enabled:
                self.dialect.model().item(self.dialect.count() - 1).setEnabled(False)
        self.host = QLineEdit("localhost")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(3306)
        self.user = QLineEdit("root")
        self.dialect.currentIndexChanged.connect(self._dialect_changed)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.save_password = QCheckBox("Passwort in der Windows-Anmeldeinformationsverwaltung speichern")
        self.save_password.setChecked(True)

        form = QFormLayout()
        form.addRow("Gespeichert:", self.saved)
        form.addRow("Name:", self.name)
        form.addRow("Dialekt:", self.dialect)
        host_row = QHBoxLayout()
        host_row.addWidget(self.host, 1)
        host_row.addWidget(QLabel("Port:"))
        host_row.addWidget(self.port)
        form.addRow("Server:", host_row)
        form.addRow("Benutzer:", self.user)
        form.addRow("Passwort:", self.password)
        form.addRow("", self.save_password)
        connection_box = QGroupBox("Verbindung")
        connection_box.setLayout(form)

        # Datenbanken
        self.connect_button = QPushButton("Verbinden")
        self.connect_button.setDefault(True)
        self.connect_button.clicked.connect(self.connect_to_server)
        self.status = QLabel("Verbinden, um die Datenbanken des Servers anzuzeigen.")
        self.status.setWordWrap(True)
        self.database_list = QListWidget()
        self.database_list.itemChanged.connect(self._update_ok)
        self.add_to_diagram = QCheckBox("Zum aktuellen Diagramm hinzufügen")
        self.add_to_diagram.setEnabled(can_add)
        self.add_to_diagram.setChecked(can_add)
        db_layout = QVBoxLayout()
        top = QHBoxLayout()
        top.addWidget(self.connect_button)
        top.addWidget(self.status, 1)
        db_layout.addLayout(top)
        db_layout.addWidget(self.database_list)
        db_layout.addWidget(self.add_to_diagram)
        db_box = QGroupBox("Datenbanken")
        db_box.setLayout(db_layout)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Einlesen")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(connection_box)
        layout.addWidget(db_box, 1)
        layout.addWidget(self.buttons)

        if self._profiles:
            self.saved.setCurrentIndex(1)
        self._update_ok()

    # ------------------------------------------------------------------ Profil

    def _dialect_changed(self, _index: int) -> None:
        """Standard-Port und -Benutzer übernehmen, solange der Nutzer sie nicht geändert hat."""
        dialect = Dialect(self.dialect.currentData())
        if self.port.value() in {port for _, _, port, _ in DIALECTS}:
            self.port.setValue(next(port for d, _, port, _ in DIALECTS if d is dialect))
        if self.user.text() in DEFAULT_USERS.values():
            self.user.setText(DEFAULT_USERS[dialect])
        self.database_list.clear()

    def _fill_from_saved(self, index: int) -> None:
        if index <= 0:
            return
        p = self._profiles[index - 1]
        self.name.setText(p.name)
        self.dialect.setCurrentIndex(self.dialect.findData(p.dialect.value))
        self.host.setText(p.host)
        self.port.setValue(p.port or next(port for d, _, port, _ in DIALECTS if d is p.dialect))
        self.user.setText(p.user or "")
        stored = credentials.get_password(p)
        self.password.setText(stored or "")
        self.database_list.clear()

    def profile(self) -> ConnectionProfile:
        """Profil aus den Eingabefeldern; Name wird bei Bedarf vergeben."""
        user, host = self.user.text().strip(), self.host.text().strip()
        dialect = Dialect(self.dialect.currentData())
        default_port = next(port for d, _, port, _ in DIALECTS if d is dialect)
        port = self.port.value()
        name = self.name.text().strip() or (f"{user}@{host}" + (f":{port}" if port != default_port else ""))
        return ConnectionProfile(name, dialect, host, None if port == default_port else port, user or None)

    # ------------------------------------------------------------------ Verbinden

    def connect_to_server(self) -> bool:
        """Verbinden und Datenbanken auflisten; True bei Erfolg."""
        self.database_list.clear()
        try:
            with self._adapter_factory(self.profile(), self.password.text()) as adapter:
                names = adapter.list_databases()
        except (AdapterError, OSError) as exc:
            self.status.setText(f"<span style='color:#B42318'>Fehler: {exc}</span>")
            self._update_ok()
            return False
        for name in names:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if name in self._preselect else Qt.CheckState.Unchecked)
            self.database_list.addItem(item)
        self.status.setText(f"{len(names)} Datenbank(en) gefunden – bitte ankreuzen.")
        self._update_ok()
        return True

    def selected_databases(self) -> list[str]:
        return [
            self.database_list.item(i).text()
            for i in range(self.database_list.count())
            if self.database_list.item(i).checkState() == Qt.CheckState.Checked
        ]

    def _update_ok(self, *_args) -> None:
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(self.selected_databases()))

    def choice(self) -> ConnectionChoice:
        return ConnectionChoice(
            profile=self.profile(),
            password=self.password.text(),
            save_password=self.save_password.isChecked(),
            databases=self.selected_databases(),
            add_to_diagram=self.add_to_diagram.isChecked() and self.add_to_diagram.isEnabled(),
        )
