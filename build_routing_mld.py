#!/usr/bin/env python3
"""Precompute exact per-cell MLD shortcuts for an Islo routing graph."""

from __future__ import annotations

import argparse
import heapq
import math
import sqlite3
from collections import defaultdict


PROFILE_BICYCLE = 0
PROFILE_BALANCED = 1
PROFILE_SHORTEST = 2


def edge_cost(row: tuple, profile: int) -> float:
    _, _, meters, stored, cycleway, dismount = row
    if profile == PROFILE_BICYCLE:
        return stored
    if profile == PROFILE_BALANCED:
        if dismount:
            return meters * 3.2
        multiplier = min(max(stored / max(meters, 1.0), 0.95), 1.45)
        return meters * (0.72 if cycleway else multiplier)
    if dismount:
        return meters * 3.5
    multiplier = min(max(stored / max(meters, 1.0), 1.0), 1.25)
    return meters * (0.92 if cycleway else multiplier)


def cell_bounds(cell: int, scale: int) -> tuple[float, float, float, float]:
    lat_index, lon_index = divmod(cell, 10_000)
    return (
        lat_index / scale - 90.0,
        (lat_index + 1) / scale - 90.0,
        lon_index / scale - 180.0,
        (lon_index + 1) / scale - 180.0,
    )


def load_cell_graph(database: sqlite3.Connection, cell: int) -> dict[int, list[tuple]]:
    south, north, west, east = cell_bounds(cell, 20)
    rows = database.execute(
        """
        SELECT e.src,e.dst,e.meters,e.cost,e.is_cycleway,e.is_dismount
        FROM nodes a JOIN edges e ON e.src=a.id JOIN nodes b ON b.id=e.dst
        WHERE a.lat>=? AND a.lat<? AND a.lon>=? AND a.lon<?
          AND b.lat>=? AND b.lat<? AND b.lon>=? AND b.lon<?
        """,
        (south, north, west, east, south, north, west, east),
    )
    adjacency: dict[int, list[tuple]] = defaultdict(list)
    for row in rows:
        adjacency[row[0]].append(row)
    return adjacency


def shortcuts_from(
    source: int,
    portals: set[int],
    adjacency: dict[int, list[tuple]],
    profile: int,
) -> list[tuple[int, float, float, list[int]]]:
    queue = [(0.0, source)]
    costs = {source: 0.0}
    meters = {source: 0.0}
    previous: dict[int, int] = {}
    settled: set[int] = set()
    remaining = portals - {source}
    results = []
    while queue and remaining:
        current_cost, node = heapq.heappop(queue)
        if node in settled:
            continue
        settled.add(node)
        if node in remaining:
            path = [node]
            while path[-1] != source:
                path.append(previous[path[-1]])
            path.reverse()
            results.append((node, current_cost, meters[node], path))
            remaining.remove(node)
        for edge in adjacency.get(node, ()):
            destination = edge[1]
            candidate = current_cost + edge_cost(edge, profile)
            if candidate >= costs.get(destination, math.inf):
                continue
            costs[destination] = candidate
            meters[destination] = meters[node] + edge[2]
            previous[destination] = node
            heapq.heappush(queue, (candidate, destination))
    return results


def build_level_zero(database: sqlite3.Connection, commit_every: int) -> None:
    database.execute("DELETE FROM hierarchy_shortcuts WHERE level=0")
    cells = [row[0] for row in database.execute(
        "SELECT level0_cell FROM hierarchy_portals GROUP BY level0_cell HAVING count(*)>1"
    )]
    inserted = 0
    for index, cell in enumerate(cells, 1):
        portals = {row[0] for row in database.execute(
            "SELECT node_id FROM hierarchy_portals WHERE level0_cell=?", (cell,)
        )}
        adjacency = load_cell_graph(database, cell)
        pending = []
        for profile in (PROFILE_BICYCLE, PROFILE_BALANCED, PROFILE_SHORTEST):
            for source in portals:
                for destination, cost, meters, path in shortcuts_from(
                    source, portals, adjacency, profile
                ):
                    pending.append((0, cell, profile, source, destination,
                                    cost, meters))
        database.executemany(
            "INSERT OR REPLACE INTO hierarchy_shortcuts VALUES(?,?,?,?,?,?,?)",
            pending,
        )
        inserted += len(pending)
        if index % commit_every == 0:
            database.commit()
            print(f"mld.level0 cells={index}/{len(cells)} shortcuts={inserted}", flush=True)
    database.commit()
    database.execute(
        "INSERT OR REPLACE INTO metadata VALUES('mldLevel0Shortcuts',?)", (str(inserted),)
    )
    database.commit()


def load_level_one_graph(
    database: sqlite3.Connection, cell: int, profile: int
) -> dict[int, list[tuple[int, float, float]]]:
    """Load the exact level-0 overlay contained by one level-1 cell."""
    adjacency: dict[int, list[tuple[int, float, float]]] = defaultdict(list)
    for source, destination, cost, meters in database.execute(
        """
        SELECT s.src,s.dst,s.cost,s.meters
        FROM hierarchy_shortcuts s
        JOIN hierarchy_portals p ON p.node_id=s.src
        JOIN hierarchy_portals q ON q.node_id=s.dst
        WHERE s.level=0 AND s.profile=?
          AND p.level1_cell=? AND q.level1_cell=?
        """,
        (profile, cell, cell),
    ):
        adjacency[source].append((destination, cost, meters))

    # A level-0 shortcut ends at a cell boundary. Preserve the original directed
    # edge that crosses that boundary so the overlay remains connected without
    # admitting ordinary interior road vertices into the upper level.
    for row in database.execute(
        """
        SELECT e.src,e.dst,e.meters,e.cost,e.is_cycleway,e.is_dismount
        FROM edges e
        JOIN hierarchy_portals p ON p.node_id=e.src
        JOIN hierarchy_portals q ON q.node_id=e.dst
        WHERE p.level1_cell=? AND q.level1_cell=?
          AND p.level0_cell!=q.level0_cell
        """,
        (cell, cell),
    ):
        adjacency[row[0]].append((row[1], edge_cost(row, profile), row[2]))
    return adjacency


def overlay_shortcuts_from(
    source: int,
    portals: set[int],
    adjacency: dict[int, list[tuple[int, float, float]]],
) -> list[tuple[int, float, float]]:
    queue = [(0.0, source)]
    costs = {source: 0.0}
    meters = {source: 0.0}
    settled: set[int] = set()
    remaining = portals - {source}
    results = []
    while queue and remaining:
        current_cost, node = heapq.heappop(queue)
        if node in settled:
            continue
        settled.add(node)
        if node in remaining:
            results.append((node, current_cost, meters[node]))
            remaining.remove(node)
        for destination, edge_weight, edge_meters in adjacency.get(node, ()):
            candidate = current_cost + edge_weight
            if candidate >= costs.get(destination, math.inf):
                continue
            costs[destination] = candidate
            meters[destination] = meters[node] + edge_meters
            heapq.heappush(queue, (candidate, destination))
    return results


def build_level_one(database: sqlite3.Connection, commit_every: int) -> None:
    database.execute("DELETE FROM hierarchy_shortcuts WHERE level=1")
    cells = [row[0] for row in database.execute(
        "SELECT level1_cell FROM hierarchy_level1_portals GROUP BY level1_cell HAVING count(*)>1"
    )]
    inserted = 0
    for index, cell in enumerate(cells, 1):
        portals = {row[0] for row in database.execute(
            "SELECT node_id FROM hierarchy_level1_portals WHERE level1_cell=?", (cell,)
        )}
        pending = []
        for profile in (PROFILE_BICYCLE, PROFILE_BALANCED, PROFILE_SHORTEST):
            adjacency = load_level_one_graph(database, cell, profile)
            for source in portals:
                for destination, cost, meters in overlay_shortcuts_from(
                    source, portals, adjacency
                ):
                    pending.append((1, cell, profile, source, destination, cost, meters))
        database.executemany(
            "INSERT OR REPLACE INTO hierarchy_shortcuts VALUES(?,?,?,?,?,?,?)", pending
        )
        inserted += len(pending)
        if index % commit_every == 0:
            database.commit()
            print(f"mld.level1 cells={index}/{len(cells)} shortcuts={inserted}", flush=True)
    database.commit()
    database.execute(
        "INSERT OR REPLACE INTO metadata VALUES('mldLevel1Shortcuts',?)", (str(inserted),)
    )
    database.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database")
    parser.add_argument("--commit-every", type=int, default=20)
    args = parser.parse_args()
    database = sqlite3.connect(args.database)
    try:
        database.execute("PRAGMA journal_mode=WAL")
        database.execute("PRAGMA synchronous=NORMAL")
        build_level_zero(database, max(1, args.commit_every))
        build_level_one(database, max(1, args.commit_every))
        database.execute("ANALYZE hierarchy_shortcuts")
        database.commit()
    finally:
        database.close()


if __name__ == "__main__":
    main()
