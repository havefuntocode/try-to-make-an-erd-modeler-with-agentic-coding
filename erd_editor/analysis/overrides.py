"""Manuelle Überschreibungen abgeleiteter Beziehungseigenschaften.

Nicht alles lässt sich aus dem Schema ableiten:
- „Elternteil hat mindestens ein Kind“ (Mindestkardinalität der Kindseite)
- identifizierend ja/nein, z. B. um eine schwache Entität zu kennzeichnen,
  deren FK nicht im Primärschlüssel liegt

Überschreibungen gehören zum Dokument (nicht zum Layout einer Ansicht)
und werden über (Kindtabelle, FK-Name) adressiert.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from erd_editor.analysis.relationships import Cardinality, Relationship
from erd_editor.model import TableRef

RelationshipKey = tuple[TableRef, str]  # (Kindtabelle, FK-Name)


@dataclass(frozen=True)
class RelationshipOverride:
    """`None` heißt jeweils: automatisch ableiten."""

    child_min_one: bool | None = None  # True: jedes Elternteil hat mindestens ein Kind
    identifying: bool | None = None

    @property
    def is_empty(self) -> bool:
        return self.child_min_one is None and self.identifying is None


def relationship_key(rel: Relationship) -> RelationshipKey:
    return (rel.child, rel.foreign_key.name)


def apply_override(rel: Relationship, override: RelationshipOverride | None) -> Relationship:
    """Beziehung mit angewandter Überschreibung (`manual=True`, wenn etwas überschrieben ist)."""
    if override is None or override.is_empty:
        return rel
    child_end = rel.child_end
    if override.child_min_one is not None:
        child_end = Cardinality(1 if override.child_min_one else 0, child_end.maximum)
    identifying = rel.identifying if override.identifying is None else override.identifying
    return replace(rel, child_end=child_end, identifying=identifying, manual=True)


def apply_overrides(
    relationships: list[Relationship], overrides: dict[RelationshipKey, RelationshipOverride]
) -> list[Relationship]:
    return [apply_override(r, overrides.get(relationship_key(r))) for r in relationships]


def describe_override(override: RelationshipOverride) -> str:
    """Kurzbeschreibung für Tooltip und Rückgängig-Menü."""
    parts = []
    if override.child_min_one is not None:
        parts.append("mindestens ein Kind" if override.child_min_one else "Kinder optional")
    if override.identifying is not None:
        parts.append("identifizierend" if override.identifying else "nicht identifizierend")
    return ", ".join(parts)
