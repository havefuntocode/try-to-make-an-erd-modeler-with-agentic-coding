"""Auto-Layout in Schichten (vereinfachtes Sugiyama-Verfahren).

Referenzierte Tabellen (Eltern) stehen links, abhängige Tabellen (Kinder)
in Spalten rechts davon. So verlaufen die meisten Linien zwischen
benachbarten Spalten, was zur rechtwinkligen Linienführung passt.

Ablauf je Datenbank und je zusammenhängender Komponente:
1. Spalten zuweisen (längster Weg; Zyklen über Zusammenhangskomponenten)
2. Reihenfolge in den Spalten per Baryzentrum-Verfahren (weniger Kreuzungen)
3. Senkrechte Ausrichtung: jede Box Richtung Mitte ihrer Nachbarn
Tabellen ohne Beziehungen kommen als Raster unter die Komponenten.

Ohne Qt-Abhängigkeit: Eingabe sind Größen und Kanten, Ausgabe Positionen.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Hashable, Iterable
from dataclasses import dataclass

import networkx as nx


@dataclass(frozen=True)
class LayoutNode:
    key: Hashable
    width: float
    height: float
    group: Hashable = None  # z. B. Datenbankname; Gruppen werden getrennt angeordnet


@dataclass(frozen=True)
class LayoutOptions:
    column_gap: float = 130.0  # waagerecht zwischen Spalten (Platz für Linien und Symbole)
    row_gap: float = 36.0  # senkrecht zwischen Boxen einer Spalte
    component_gap: float = 90.0  # zwischen unabhängigen Komponenten
    group_gap: float = 170.0  # zwischen Gruppen (Platz für die Behälter samt Kopfzeile)
    ordering_sweeps: int = 8
    alignment_rounds: int = 6
    isolated_columns: int = 4  # Mindestspaltenzahl im Raster für Tabellen ohne Beziehungen
    aspect_ratio: float = 1.6  # angestrebtes Verhältnis Breite:Höhe einer Gruppe (Querformat)


Position = tuple[float, float]  # linke obere Ecke


def layered_layout(
    nodes: Iterable[LayoutNode],
    edges: Iterable[tuple[Hashable, Hashable]],
    options: LayoutOptions | None = None,
) -> dict[Hashable, Position]:
    """Positionen (linke obere Ecke) für alle Knoten berechnen.

    `edges` sind Paare (Kind, Elternteil), also in FK-Richtung.
    Selbstreferenzen und Kanten zwischen Gruppen beeinflussen das Layout nicht.
    Das Ergebnis ist deterministisch (gleiche Eingabe → gleiche Positionen).
    """
    opt = options or LayoutOptions()
    node_list = list(nodes)
    by_key = {n.key: n for n in node_list}

    groups: dict[Hashable, list[LayoutNode]] = {}
    for n in node_list:
        groups.setdefault(n.group, []).append(n)

    group_edges: dict[Hashable, list[tuple[Hashable, Hashable]]] = defaultdict(list)
    for child, parent in edges:
        if child == parent or child not in by_key or parent not in by_key:
            continue
        if by_key[child].group == by_key[parent].group:
            group_edges[by_key[child].group].append((child, parent))

    result: dict[Hashable, Position] = {}
    y_offset = 0.0
    for group, members in groups.items():
        positions, height = _layout_group(members, group_edges[group], opt)
        for key, (x, y) in positions.items():
            result[key] = (x, y + y_offset)
        y_offset += height + opt.group_gap
    return result


# ---------------------------------------------------------------------- Gruppe


def _layout_group(
    members: list[LayoutNode], edges: list[tuple[Hashable, Hashable]], opt: LayoutOptions
) -> tuple[dict[Hashable, Position], float]:
    """Eine Gruppe anordnen; liefert Positionen (ab 0/0) und Gesamthöhe."""
    order = {n.key: i for i, n in enumerate(members)}  # Modellreihenfolge für Determinismus
    graph = nx.DiGraph()
    graph.add_nodes_from(n.key for n in members)
    graph.add_edges_from((parent, child) for child, parent in edges)  # Eltern → Kind = links → rechts

    connected = [n for n in members if graph.degree(n.key) > 0]
    isolated = [n for n in members if graph.degree(n.key) == 0]
    sizes = {n.key: n for n in members}

    components = [
        sorted(c, key=order.__getitem__)
        for c in nx.weakly_connected_components(graph.subgraph(n.key for n in connected))
    ]
    components.sort(key=lambda c: (-len(c), order[c[0]]))

    # Komponenten einzeln anordnen, dann zeilenweise auf eine Zielbreite verteilen
    blocks = [_layout_component(graph.subgraph(c), c, sizes, order, opt) for c in components]
    target = _target_width(members, [w for _, w, _ in blocks], opt)

    positions: dict[Hashable, Position] = {}
    x = y = row_h = 0.0
    for comp_pos, comp_w, comp_h in blocks:
        if x > 0 and x + comp_w > target:  # neue Zeile
            x, y, row_h = 0.0, y + row_h + opt.component_gap, 0.0
        for key, (px, py) in comp_pos.items():
            positions[key] = (px + x, py + y)
        x += comp_w + opt.component_gap
        row_h = max(row_h, comp_h)
    y += row_h

    if isolated:
        if blocks:
            y += opt.component_gap
        grid_pos, grid_h = _grid(isolated, target, opt)
        for key, (px, py) in grid_pos.items():
            positions[key] = (px, py + y)
        y += grid_h
    return positions, max(y, 0.0)


def _target_width(members: list[LayoutNode], component_widths: list[float], opt: LayoutOptions) -> float:
    """Zielbreite einer Gruppe für ein Seitenverhältnis von etwa `aspect_ratio`.

    Grundlage ist die Gesamtfläche aller Boxen samt Abständen; die breiteste
    Komponente wird nie umgebrochen.
    """
    gap = opt.component_gap
    area = sum((n.width + gap) * (n.height + gap) for n in members)
    return max([math.sqrt(area * opt.aspect_ratio), *component_widths])


# ---------------------------------------------------------------------- Komponente


def _layout_component(graph, keys, sizes, order, opt):
    layers = _assign_layers(graph, order)
    columns = _order_columns(graph, layers, order, opt.ordering_sweeps)

    # x je Spalte
    xs: list[float] = []
    x = 0.0
    for column in columns:
        xs.append(x)
        x += max(sizes[k].width for k in column) + opt.column_gap
    total_w = x - opt.column_gap

    # y: erst stapeln, dann Richtung Nachbarn ausrichten
    centers: dict[Hashable, float] = {}
    for column in columns:
        y = 0.0
        for k in column:
            centers[k] = y + sizes[k].height / 2
            y += sizes[k].height + opt.row_gap
    undirected = graph.to_undirected(as_view=True)
    for _ in range(opt.alignment_rounds):
        for column in columns:
            desired = []
            for k in column:
                neighbours = [centers[n] for n in undirected.neighbors(k) if n != k]
                desired.append(sum(neighbours) / len(neighbours) if neighbours else centers[k])
            for k, c in zip(column, _resolve_overlaps(column, desired, sizes, opt.row_gap)):
                centers[k] = c

    top = min(centers[k] - sizes[k].height / 2 for k in keys)
    bottom = max(centers[k] + sizes[k].height / 2 for k in keys)
    positions = {}
    for col_index, column in enumerate(columns):
        for k in column:
            positions[k] = (xs[col_index], centers[k] - sizes[k].height / 2 - top)
    return positions, total_w, bottom - top


def _assign_layers(graph: nx.DiGraph, order: dict) -> dict[Hashable, int]:
    """Spaltennummer je Knoten: längster Weg von den Wurzeln (Eltern ohne Eltern).

    Zyklen werden über die Zusammenhangskomponenten (SCC) aufgelöst; Tabellen
    eines Zyklus landen in derselben Spalte. Reine Elterntabellen werden so
    weit nach rechts gezogen, wie ihre Kinder es zulassen (kürzere Linien).
    """
    condensed = nx.condensation(graph)
    members = condensed.graph["mapping"]  # Knoten → SCC-Nummer
    scc_layer: dict[int, int] = {}
    for scc in nx.topological_sort(condensed):
        preds = list(condensed.predecessors(scc))
        scc_layer[scc] = 1 + max(scc_layer[p] for p in preds) if preds else 0
    for scc in condensed.nodes:
        succs = list(condensed.successors(scc))
        if not list(condensed.predecessors(scc)) and succs:
            scc_layer[scc] = min(scc_layer[s] for s in succs) - 1
    return {k: scc_layer[members[k]] for k in graph.nodes}


def _order_columns(graph: nx.DiGraph, layers: dict, order: dict, sweeps: int) -> list[list[Hashable]]:
    """Spalten bilden und per Baryzentrum sortieren (abwechselnd vor/zurück)."""
    count = max(layers.values()) + 1
    columns: list[list[Hashable]] = [[] for _ in range(count)]
    for k in sorted(graph.nodes, key=order.__getitem__):
        columns[layers[k]].append(k)
    undirected = graph.to_undirected(as_view=True)

    def sort_by(column_index: int, reference_index: int) -> None:
        ref = {k: i for i, k in enumerate(columns[reference_index])}
        current = {k: i for i, k in enumerate(columns[column_index])}

        def key(k):
            linked = [ref[n] for n in undirected.neighbors(k) if n in ref]
            return (sum(linked) / len(linked) if linked else current[k], current[k])

        columns[column_index].sort(key=key)

    for sweep in range(sweeps):
        if sweep % 2 == 0:
            for i in range(1, count):
                sort_by(i, i - 1)
        else:
            for i in range(count - 2, -1, -1):
                sort_by(i, i + 1)
    return columns


def _resolve_overlaps(column: list, desired: list[float], sizes: dict, gap: float) -> list[float]:
    """Mittelpunkte möglichst nah an `desired`, Reihenfolge fest, Mindestabstand `gap`.

    Vorwärts nach unten schieben, rückwärts nach oben ziehen, dann
    nochmals vorwärts, damit garantiert nichts überlappt.
    """
    heights = [sizes[k].height for k in column]
    tops = [d - h / 2 for d, h in zip(desired, heights)]
    for i in range(1, len(tops)):
        tops[i] = max(tops[i], tops[i - 1] + heights[i - 1] + gap)
    for i in range(len(tops) - 2, -1, -1):
        tops[i] = min(tops[i], tops[i + 1] - heights[i] - gap)
    for i in range(1, len(tops)):
        tops[i] = max(tops[i], tops[i - 1] + heights[i - 1] + gap)
    return [t + h / 2 for t, h in zip(tops, heights)]


# ---------------------------------------------------------------------- Raster


def _grid(nodes: list[LayoutNode], available_width: float, opt: LayoutOptions) -> tuple[dict[Hashable, Position], float]:
    """Tabellen ohne Beziehungen in einem Raster mit gleich breiten Spalten.

    So viele Spalten, wie in `available_width` passen (mindestens
    `isolated_columns`, höchstens so viele wie Tabellen).
    """
    col_w = max(n.width for n in nodes) + opt.row_gap * 1.5
    per_row = max(opt.isolated_columns, int((available_width + opt.row_gap * 1.5) // col_w))
    per_row = max(1, min(per_row, len(nodes)))
    positions: dict[Hashable, Position] = {}
    y = 0.0
    for start in range(0, len(nodes), per_row):
        row = nodes[start : start + per_row]
        for i, n in enumerate(row):
            positions[n.key] = (i * col_w, y)
        y += max(n.height for n in row) + opt.row_gap
    return positions, y - opt.row_gap
