"""Passwörter für Verbindungsprofile – ausschließlich über `keyring`.

Unter Windows landen sie in der Windows-Anmeldeinformationsverwaltung,
nie in JSON-Dateien oder im Klartext.
"""

from __future__ import annotations

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from erd_editor.model import ConnectionProfile

SERVICE = "erd-editor"


def account(profile: ConnectionProfile) -> str:
    """Kontoname im Tresor, z. B. `mysql://root@localhost:3306`."""
    port = f":{profile.port}" if profile.port else ""
    return f"{profile.dialect.value}://{profile.user or ''}@{profile.host}{port}"


def get_password(profile: ConnectionProfile) -> str | None:
    try:
        return keyring.get_password(SERVICE, account(profile))
    except KeyringError:
        return None


def set_password(profile: ConnectionProfile, password: str) -> None:
    keyring.set_password(SERVICE, account(profile), password)


def delete_password(profile: ConnectionProfile) -> None:
    try:
        keyring.delete_password(SERVICE, account(profile))
    except PasswordDeleteError:
        pass
