"""Gemeinsame Schnittstelle aller Dialekt-Adapter.

Ein Adapter liest ein Schema aus der Datenbank und liefert es als
neutrales Modell. Er ist die einzige Stelle, die den Dialekt kennt.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from types import TracebackType

from erd_editor.model import ConnectionProfile, Database, Dialect


class AdapterError(Exception):
    """Fehler beim Verbinden oder Einlesen."""


class SchemaAdapter(ABC):
    """Basisklasse für Dialekt-Adapter.

    Das Passwort wird beim Erzeugen übergeben (vom Aufrufer aus dem
    `keyring` geholt) und nirgends gespeichert.
    Verwendung als Kontextmanager öffnet und schließt die Verbindung.
    """

    dialect: Dialect
    # Profil-Dialekte, die der Adapter bedient (Standard: nur `dialect`)
    dialects: tuple[Dialect, ...] = ()

    def __init__(self, profile: ConnectionProfile, password: str | None = None) -> None:
        if profile.dialect not in (self.dialects or (self.dialect,)):
            raise AdapterError(
                f"Profil {profile.name!r} ist für {profile.dialect.value}, "
                f"Adapter für {self.dialect.value}"
            )
        self.profile = profile
        self._password = password

    @abstractmethod
    def connect(self) -> None:
        """Verbindung aufbauen."""

    @abstractmethod
    def close(self) -> None:
        """Verbindung schließen; mehrfacher Aufruf ist erlaubt."""

    @abstractmethod
    def list_databases(self) -> list[str]:
        """Namen der Benutzerdatenbanken (ohne Systemdatenbanken)."""

    @abstractmethod
    def read_database(self, name: str) -> Database:
        """Eine Datenbank vollständig als neutrales Modell einlesen."""

    def __enter__(self) -> SchemaAdapter:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
