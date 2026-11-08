"""Austauschbare Notationen für Linienenden."""

from erd_editor.notation.base import Notation
from erd_editor.notation.crowsfoot import CrowsFootNotation
from erd_editor.notation.idef1x import Idef1xNotation
from erd_editor.notation.minmax import MinMaxNotation

NOTATIONS: dict[str, type[Notation]] = {
    CrowsFootNotation.key: CrowsFootNotation,
    MinMaxNotation.key: MinMaxNotation,
    Idef1xNotation.key: Idef1xNotation,
}


def notation_for(key: str) -> Notation:
    """Notation zum gespeicherten Schlüssel; Unbekanntes fällt auf Krähenfuß zurück."""
    return NOTATIONS.get(key, CrowsFootNotation)()


__all__ = ["NOTATIONS", "CrowsFootNotation", "Idef1xNotation", "MinMaxNotation", "Notation", "notation_for"]
