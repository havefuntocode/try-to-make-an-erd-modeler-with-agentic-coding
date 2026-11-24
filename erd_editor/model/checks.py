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
