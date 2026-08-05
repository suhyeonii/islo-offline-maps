#!/usr/bin/env python3
"""Build an Islo bicycle-routing SQLite graph from an osmium OPL stream."""

import argparse
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
    args = parser.parse_args()

    database = sqlite3.connect(args.output)
    database.executescript("""
      PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA temp_store=MEMORY;
      CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE nodes(id INTEGER PRIMARY KEY, lat REAL NOT NULL, lon REAL NOT NULL);
      CREATE TABLE edges(src INTEGER NOT NULL, dst INTEGER NOT NULL,
                         meters REAL NOT NULL, cost REAL NOT NULL,
                         PRIMARY KEY(src, dst)) WITHOUT ROWID;
    """)
    database.execute("INSERT INTO metadata VALUES('schemaVersion','1')")
    database.execute("INSERT INTO metadata VALUES('kind',?)", ("compact" if args.compact else "detail",))
    coordinates: dict[int, tuple[float, float]] = {}
    pending_nodes: list[tuple[int, float, float]] = []
    pending_edges: list[tuple[int, int, float, float]] = []

    for line in sys.stdin:
        if line.startswith("n"):
            fields = line.split()
            node_id = int(fields[0][1:])
            lon = next((float(value[1:]) for value in fields if value.startswith("x")), None)
            lat = next((float(value[1:]) for value in fields if value.startswith("y")), None)
            if lat is not None and lon is not None:
                coordinates[node_id] = (lat, lon)
                pending_nodes.append((node_id, lat, lon))
                if len(pending_nodes) >= 50_000:
                    database.executemany("INSERT OR IGNORE INTO nodes VALUES(?,?,?)", pending_nodes)
                    pending_nodes.clear()
        elif line.startswith("w"):
            match = WAY_RE.match(line.rstrip())
            if not match:
                continue
            values = tags(match.group(1))
            weight = penalty(values, args.compact)
            if weight is None:
                continue
            refs = [int(value.lstrip("n")) for value in match.group(2).split(",")]
            oneway = values.get("oneway") in {"yes", "1", "true"}
            reverse = values.get("oneway") == "-1"
            for source, destination in zip(refs, refs[1:]):
                if source not in coordinates or destination not in coordinates:
                    continue
                meters = haversine(coordinates[source], coordinates[destination])
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
    database.commit()
    database.close()


if __name__ == "__main__":
    main()
