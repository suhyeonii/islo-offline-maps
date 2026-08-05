#!/usr/bin/env python3
"""Build an Islo bicycle-routing SQLite graph from an osmium OPL stream."""

import argparse
import csv
import math
import re
import sqlite3
import sys
from urllib.parse import unquote


WAY_RE = re.compile(r"^w-?\d+ .* T(.*?) N(.*)$")


def tags(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    if not raw:
        return result
    for item in raw.split(","):
        key, separator, value = item.partition("=")
        if separator:
            result[key] = unquote(value)
    return result


def penalty(values: dict[str, str], compact: bool) -> float | None:
    highway = values.get("highway", "")
    bicycle = values.get("bicycle", "")
    if bicycle in {"no", "private"} or highway in {"motorway", "motorway_link", "steps"}:
        return None
    if highway == "footway":
        # Keep pedestrian-only paths out of bicycle routing, but preserve mapped
        # riverside entrances and crossings where OSM explicitly permits bicycles.
        if bicycle not in {"yes", "designated", "official"}:
            return None
        return 0.62 if bicycle == "yes" else 0.48
    dedicated = highway == "cycleway" or bicycle in {"designated", "official"}
    if compact and not dedicated and highway not in {
        "trunk", "primary", "secondary", "tertiary", "track", "path"
    }:
        return None
    base = {
        "cycleway": 0.42, "path": 0.55, "track": 0.65,
        "living_street": 0.82, "residential": 0.9, "service": 1.0,
        "unclassified": 1.05, "tertiary": 1.2, "secondary": 1.65,
        "primary": 2.2, "trunk": 3.0,
    }.get(highway)
    if base is None:
        return None
    return min(base, 0.48) if dedicated else base


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 12_742_000 * math.asin(math.sqrt(value))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--official-csv")
    args = parser.parse_args()

    route_names = {
        1: "아라자전거길", 2: "한강종주자전거길", 3: "남한강자전거길",
        4: "새재자전거길", 5: "낙동강자전거길", 6: "금강자전거길",
        7: "영산강자전거길", 8: "북한강자전거길", 9: "섬진강자전거길",
        10: "오천자전거길", 11: "동해안(강원)자전거길",
        12: "동해안(경북)자전거길", 13: "제주환상자전거길",
    }
    official_points = []
    official_grid = {}
    official_nearest = {}
    grid_size = .002
    if args.official_csv:
        with open(args.official_csv, encoding="utf-8-sig", newline="") as source:
            for row in csv.DictReader(source):
                try:
                    sequence = int(row["순서"])
                    route_id = int(row["국토종주 자전거길"])
                    latitude = float(row["위도(LINE_XP)"])
                    longitude = float(row["경도(LINE_YP)"])
                except (KeyError, TypeError, ValueError):
                    continue
                if route_id not in route_names:
                    continue
                index = len(official_points)
                official_points.append((sequence, route_id, latitude, longitude))
                key = (int(latitude / grid_size), int(longitude / grid_size))
                official_grid.setdefault(key, []).append(index)

    database = sqlite3.connect(args.output)
    database.executescript("""
      PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA temp_store=MEMORY;
      CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE nodes(id INTEGER PRIMARY KEY, lat REAL NOT NULL, lon REAL NOT NULL);
      CREATE TABLE edges(src INTEGER NOT NULL, dst INTEGER NOT NULL,
                         meters REAL NOT NULL, cost REAL NOT NULL,
                         PRIMARY KEY(src, dst)) WITHOUT ROWID;
      CREATE TABLE official_routes(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
      CREATE TABLE official_nodes(node_id INTEGER NOT NULL, route_id INTEGER NOT NULL,
                                  sequence INTEGER NOT NULL,
                                  PRIMARY KEY(node_id, route_id)) WITHOUT ROWID;
    """)
    database.execute("INSERT INTO metadata VALUES('schemaVersion','1')")
    database.execute("INSERT INTO metadata VALUES('kind',?)", ("compact" if args.compact else "detail",))
    pending_nodes: list[tuple[int, float, float]] = []
    pending_edges: list[tuple[int, int, float, float]] = []
    did_flush_nodes = False

    for line in sys.stdin:
        if line.startswith("n"):
            fields = line.split()
            node_id = int(fields[0][1:])
            lon = next((float(value[1:]) for value in fields if value.startswith("x")), None)
            lat = next((float(value[1:]) for value in fields if value.startswith("y")), None)
            if lat is not None and lon is not None:
                cell = (int(lat / grid_size), int(lon / grid_size))
                for latitude_cell in range(cell[0] - 1, cell[0] + 2):
                    for longitude_cell in range(cell[1] - 1, cell[1] + 2):
                        for index in official_grid.get((latitude_cell, longitude_cell), []):
                            _, _, route_latitude, route_longitude = official_points[index]
                            distance = (lat - route_latitude) ** 2 + (lon - route_longitude) ** 2
                            if distance <= .0015 ** 2 and distance < official_nearest.get(
                                index, (float("inf"), 0)
                            )[0]:
                                official_nearest[index] = (distance, node_id)
                pending_nodes.append((node_id, lat, lon))
                if len(pending_nodes) >= 50_000:
                    database.executemany("INSERT OR IGNORE INTO nodes VALUES(?,?,?)", pending_nodes)
                    pending_nodes.clear()
        elif line.startswith("w"):
            if not did_flush_nodes:
                database.executemany("INSERT OR IGNORE INTO nodes VALUES(?,?,?)", pending_nodes)
                pending_nodes.clear()
                database.commit()
                did_flush_nodes = True
            match = WAY_RE.match(line.rstrip())
            if not match:
                continue
            values = tags(match.group(1))
            weight = penalty(values, args.compact)
            if weight is None:
                continue
            refs = [int(value.lstrip("n")) for value in match.group(2).split(",")]
            placeholders = ",".join("?" for _ in refs)
            way_coordinates = {
                node_id: (latitude, longitude)
                for node_id, latitude, longitude in database.execute(
                    f"SELECT id,lat,lon FROM nodes WHERE id IN ({placeholders})", refs
                )
            }
            oneway = values.get("oneway") in {"yes", "1", "true"}
            reverse = values.get("oneway") == "-1"
            for source, destination in zip(refs, refs[1:]):
                if source not in way_coordinates or destination not in way_coordinates:
                    continue
                meters = haversine(way_coordinates[source], way_coordinates[destination])
                if reverse:
                    source, destination = destination, source
                pending_edges.append((source, destination, meters, meters * weight))
                if not oneway and not reverse:
                    pending_edges.append((destination, source, meters, meters * weight))
                if len(pending_edges) >= 100_000:
                    database.executemany("INSERT OR REPLACE INTO edges VALUES(?,?,?,?)", pending_edges)
                    pending_edges.clear()

    database.executemany("INSERT OR IGNORE INTO nodes VALUES(?,?,?)", pending_nodes)
    database.executemany("INSERT OR REPLACE INTO edges VALUES(?,?,?,?)", pending_edges)
    database.executescript("""
      CREATE INDEX nodes_lat ON nodes(lat);
      ANALYZE;
    """)
    if args.official_csv:
        database.executemany("INSERT INTO official_routes VALUES(?,?)", route_names.items())
        official_nodes = [
            (official_nearest[index][1], point[1], point[0])
            for index, point in enumerate(official_points) if index in official_nearest
        ]
        database.executemany(
            "INSERT OR IGNORE INTO official_nodes VALUES(?,?,?)", official_nodes
        )
        database.executescript("""
          UPDATE edges SET cost=cost*0.45
          WHERE src IN (SELECT node_id FROM official_nodes)
            AND dst IN (SELECT node_id FROM official_nodes);
          ANALYZE;
        """)
    database.commit()
    database.close()


if __name__ == "__main__":
    main()
