"""Spaltennamen in CHECK-Ausdrücken finden und umbenennen.

Der Ausdruck bleibt nativer SQL-Text des Dialekts (kein Übersetzen). Hier
wird er nur so weit zerlegt, dass Bezeichner von Zeichenketten, Funktions-
aufrufen und Typ-Casts (`::numeric`) unterschieden werden können. Bezeichner
dürfen gequotet sein (`"x"`, `` `x` ``, `[x]`); verglichen wird ohne
Beachtung der Groß-/Kleinschreibung.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

_TOKEN = re.compile(
    r"""
      (?P<string>[NnEe]?'(?:[^']|'')*')
    | (?P<dquote>"(?:[^"]|"")*")
    | (?P<backtick>`(?:[^`]|``)*`)
    | (?P<bracket>\[(?:[^\]]|\]\])*\])
    | (?P<ident>[A-Za-z_À-￿][A-Za-z0-9_$À-￿]*)
    | (?P<other>.)
    """,
    re.VERBOSE | re.DOTALL,
)

_QUOTES = {"dquote": ('"', '"'), "backtick": ("`", "`"), "bracket": ("[", "]")}


def _tokens(expression: str) -> Iterator[tuple[str, str, str | None]]:
    """(Art, Text, Bezeichner) – Bezeichner nur bei Spaltenkandidaten, sonst None."""
    items = [(m.lastgroup, m.group()) for m in _TOKEN.finditer(expression)]
    for i, (kind, text) in enumerate(items):
        name = None
        if kind == "ident" or kind in _QUOTES:
            before = "".join(t for _, t in items[max(0, i - 2):i]).replace(" ", "")
            after = next((t for k, t in items[i + 1:] if not t.isspace()), "")
            if after != "(" and not before.endswith(("::", ".")) and after != ".":
                if kind == "ident":
                    name = text
                else:
                    o, c = _QUOTES[kind]
                    name = text[1:-1].replace(c + c, c)
        yield kind, text, name


def expression_columns(expression: str, column_names: Iterable[str]) -> tuple[str, ...]:
    """Spalten der Tabelle, die im Ausdruck vorkommen (in Reihenfolge des ersten Auftretens)."""
    by_lower = {n.lower(): n for n in column_names}
    found: list[str] = []
    for _, _, name in _tokens(expression):
        col = by_lower.get(name.lower()) if name else None
        if col and col not in found:
            found.append(col)
    return tuple(found)


def rename_in_expression(expression: str, renames: dict[str, str]) -> str:
    """Spaltennamen im Ausdruck ersetzen; Quoting bleibt erhalten."""
    if not renames:
        return expression
    lower = {old.lower(): new for old, new in renames.items()}
    parts = []
    for kind, text, name in _tokens(expression):
        new = lower.get(name.lower()) if name else None
        if new is None:
            parts.append(text)
        elif kind == "ident":
            parts.append(new)
        else:
            o, c = _QUOTES[kind]
            parts.append(o + new.replace(c, c + c) + c)
    return "".join(parts)


def strip_outer_parens(expression: str) -> str:
    """`((a > 0))` → `a > 0`; nur Klammern, die den ganzen Ausdruck umschließen."""
    e = expression.strip()
    while e.startswith("(") and e.endswith(")") and _closing(e) == len(e) - 1:
        e = e[1:-1].strip()
    return e


def _closing(expression: str) -> int:
    """Position der Klammer, die die erste öffnende schließt (Zeichenketten übersprungen)."""
    depth, pos = 0, 0
    for m in _TOKEN.finditer(expression):
        t = m.group()
        if m.lastgroup == "other":
            if t == "(":
                depth += 1
            elif t == ")":
                depth -= 1
                if depth == 0:
                    return pos
        pos += len(t)
    return -1


_CHECK_PREFIX = re.compile(r"^\s*CHECK\s*", re.IGNORECASE)
_NOT_VALID = re.compile(r"\s+NOT\s+VALID\s*$", re.IGNORECASE)


def clean_check_expression(text: str) -> str:
    """Ausdruck, wie ihn eine Datenbank liefert, auf den reinen Ausdruck kürzen.

    - PostgreSQL: `CHECK ((preis > (0)::numeric)) NOT VALID` → `preis > (0)::numeric`
    - SQL Server: `([preis]>(0))` → `[preis]>(0)`
    - MySQL: ``(`preis` > 0)`` → `` `preis` > 0 ``; Zeichensatz-Präfixe vor
      Literalen (`_utf8mb4'offen'`) entfallen
    """
    e = _NOT_VALID.sub("", _CHECK_PREFIX.sub("", text.strip()))
    e = _drop_charset_introducers(e)
    return strip_outer_parens(e)


def _drop_charset_introducers(expression: str) -> str:
    parts: list[str] = []
    items = [(m.lastgroup, m.group()) for m in _TOKEN.finditer(expression)]
    for i, (kind, text) in enumerate(items):
        nxt = items[i + 1] if i + 1 < len(items) else None
        if kind == "ident" and text.startswith("_") and nxt is not None and nxt[0] == "string" \
                and nxt[1].startswith("'"):
            continue
        parts.append(text)
    return "".join(parts)


def add_check(table, name: str, raw_expression: str) -> None:
    """Check aus einer Datenbankzeile an eine eingelesene Tabelle hängen (Spalten aus dem Ausdruck)."""
    from erd_editor.model.schema import CheckConstraint

    expression = clean_check_expression(raw_expression or "")
    if expression:
        columns = expression_columns(expression, [c.name for c in table.columns])
        table.checks.append(CheckConstraint(name, expression, columns))
