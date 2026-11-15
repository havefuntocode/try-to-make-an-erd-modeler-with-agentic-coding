"""Benutzte Tabellen/Views aus dem SQL-Text einer View erraten (für modellierte Views).

Bewusst einfach: Es werden die Namen hinter FROM und JOIN gelesen –
auch mehrere durch Komma getrennte Tabellen – und mit den bekannten
Objekten der Datenbank verglichen. Gequotete Namen (`` `x` ``, `"x"`,
`[x]`) und Schema-Präfixe werden verstanden. Groß-/Kleinschreibung
spielt keine Rolle. Was nicht erkannt wird, kann man im Dialog von Hand
anhaken.
"""

from __future__ import annotations

import re

from erd_editor.model import TableRef

# ein Namensteil: `a`, "a", [a] oder a
_PART = r'(?:`(?:[^`]|``)+`|"(?:[^"]|"")+"|\[(?:[^\]]|\]\])+\]|[A-Za-z_][\w$]*)'
_NAME = rf"{_PART}(?:\s*\.\s*{_PART}){{0,2}}"
# Alias hinter dem Namen – aber kein Schlüsselwort, das die Klausel fortsetzt
_KEYWORDS = ("join|on|where|left|right|inner|outer|cross|full|natural|straight_join|using|group|order|"
             "having|union|except|intersect|limit|offset|fetch|window|for|lateral|with")
_ALIAS = rf"(?:\s+(?:as\s+)?(?!(?:{_KEYWORDS})\b)[A-Za-z_]\w*)?"
_FROM = re.compile(rf"\b(?:from|join)\s+({_NAME}{_ALIAS}(?:\s*,\s*{_NAME}{_ALIAS})*)", re.IGNORECASE)
_NAME_RE = re.compile(_NAME)
_PART_RE = re.compile(_PART)


def _unquote(part: str) -> str:
    if part[0] == "`":
        return part[1:-1].replace("``", "`")
    if part[0] == '"':
        return part[1:-1].replace('""', '"')
    if part[0] == "[":
        return part[1:-1].replace("]]", "]")
    return part


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", sql)


def referenced_names(sql: str) -> list[tuple[str, ...]]:
    """Objektnamen hinter FROM/JOIN als Namensteile, z. B. `("shop", "kunde")`."""
    found: list[tuple[str, ...]] = []
    for match in _FROM.finditer(_strip_comments(sql)):
        for item in match.group(1).split(","):
            name = _NAME_RE.match(item.strip())
            if name is None:
                continue
            parts = tuple(_unquote(p) for p in _PART_RE.findall(name.group(0)))
            if parts and parts not in found:
                found.append(parts)
    return found


def guess_dependencies(sql: str, candidates: list[TableRef]) -> list[TableRef]:
    """Bekannte Objekte, die im SQL-Text hinter FROM/JOIN vorkommen (Reihenfolge wie im Text).

    Ein Name ohne Schema passt auf ein gleichnamiges Objekt in irgendeinem
    Schema der Datenbank; mit Schema (oder Datenbank.Schema) muss auch das
    Schema stimmen.
    """
    result: list[TableRef] = []
    for parts in referenced_names(sql or ""):
        name, schema = parts[-1].lower(), (parts[-2].lower() if len(parts) >= 2 else None)
        for ref in candidates:
            if ref.table.lower() != name:
                continue
            if schema is not None and (ref.schema or ref.database).lower() != schema:
                continue
            if ref not in result:
                result.append(ref)
    return result


__all__ = ["guess_dependencies", "referenced_names"]
