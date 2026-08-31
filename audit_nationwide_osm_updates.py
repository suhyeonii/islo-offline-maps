#!/usr/bin/env python3
"""Report cycling/access features changed between two prepared OSM snapshots.

Inputs are GeoJSON Text Sequences exported with `osmium export -u type_id`.
Only actual tagged features are counted; referenced geometry nodes are ignored.
"""

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path


def records(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            line = line.strip("\x1e\n ")
            if line:
                yield json.loads(line)


def all_points(coordinates):
    if not coordinates:
        return
    if isinstance(coordinates[0], (int, float)):
        yield coordinates
    else:
        for child in coordinates:
            yield from all_points(child)


def center(feature):
    points = list(all_points(feature["geometry"]["coordinates"]))
    return sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)


def haversine(a, b):
    lon1, lat1 = map(math.radians, a); lon2, lat2 = map(math.radians, b)
    dlon, dlat = lon2 - lon1, lat2 - lat1
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 12_742_000 * math.asin(math.sqrt(value))


def line_length(coordinates):
    if not coordinates:
        return 0.0
    if isinstance(coordinates[0][0], (int, float)):
        return sum(haversine(a, b) for a, b in zip(coordinates, coordinates[1:]))
    return sum(line_length(child) for child in coordinates)


def is_cycling(feature):
    p = feature.get("properties", {})
    return (p.get("highway") == "cycleway" or p.get("bicycle") in {"designated", "official"}
            or any(key == "cycleway" or key.startswith("cycleway:") for key in p))


def access_kind(feature):
    p = feature.get("properties", {})
    if p.get("highway") == "elevator" or p.get("elevator") == "yes":
        return "elevator"
    if p.get("highway") == "steps":
        return "steps"
    return None


def load(path, predicate):
    return {feature["id"]: feature for feature in records(path) if predicate(feature)}


def region_for(point, regions):
    lon, lat = point
    matches = [r for r in regions if r["west"] <= lon <= r["east"] and r["south"] <= lat <= r["north"]]
    if not matches:
        return "outside"
    return min(matches, key=lambda r: (r["east"] - r["west"]) * (r["north"] - r["south"]))["id"]


def local_segment_distance(point, first, second):
    scale_x = 88_000 * math.cos(math.radians(point[1])) / math.cos(math.radians(37.5))
    px, py = point[0] * scale_x, point[1] * 111_000
    ax, ay = first[0] * scale_x, first[1] * 111_000
    bx, by = second[0] * scale_x, second[1] * 111_000
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    ratio = 0 if length2 == 0 else max(0, min(1, ((px-ax)*dx + (py-ay)*dy) / length2))
    return math.hypot(px - ax - ratio * dx, py - ay - ratio * dy)


def geometry_lines(coordinates):
    if not coordinates:
        return
    if isinstance(coordinates[0][0], (int, float)):
        yield coordinates
    else:
        for child in coordinates:
            yield from geometry_lines(child)


def bridge_grid(path):
    grid = defaultdict(list)
    for feature in records(path):
        for line in geometry_lines(feature["geometry"]["coordinates"]):
            for first, second in zip(line, line[1:]):
                west, east = sorted((first[0], second[0])); south, north = sorted((first[1], second[1]))
                for x in range(int(west * 200), int(east * 200) + 1):
                    for y in range(int(south * 200), int(north * 200) + 1):
                        grid[(x, y)].append((first, second))
    return grid


def near_bridge(point, grid, meters=35):
    key = (int(point[0] * 200), int(point[1] * 200))
    candidates = []
    for x in range(key[0] - 1, key[0] + 2):
        for y in range(key[1] - 1, key[1] + 2):
            candidates.extend(grid.get((x, y), ()))
    return any(local_segment_distance(point, first, second) <= meters for first, second in candidates)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cycling-old", type=Path, required=True)
    parser.add_argument("--cycling-new", type=Path, required=True)
    parser.add_argument("--access-old", type=Path, required=True)
    parser.add_argument("--access-new", type=Path, required=True)
    parser.add_argument("--bridges", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    regions = json.loads(args.manifest.read_text(encoding="utf-8"))["regions"]
    old_cycle, new_cycle = load(args.cycling_old, is_cycling), load(args.cycling_new, is_cycling)
    old_access = load(args.access_old, lambda f: access_kind(f) is not None)
    new_access = load(args.access_new, lambda f: access_kind(f) is not None)
    bridges = bridge_grid(args.bridges)
    result = {"regions": {}, "newCyclingFeatures": [], "newBridgeAccess": []}
    totals = defaultdict(lambda: defaultdict(float))
    for object_id in sorted(new_cycle.keys() - old_cycle.keys()):
        feature = new_cycle[object_id]
        region = region_for(center(feature), regions)
        length = line_length(feature["geometry"]["coordinates"])
        totals[region]["newCyclingWays"] += 1
        totals[region]["newCyclingMeters"] += length
        result["newCyclingFeatures"].append({
            "id": object_id, "region": region, "meters": round(length),
            "name": feature.get("properties", {}).get("name"),
            "highway": feature.get("properties", {}).get("highway"),
        })
    for object_id in sorted(new_access.keys() - old_access.keys()):
        feature = new_access[object_id]
        point = center(feature)
        if not near_bridge(point, bridges):
            continue
        region = region_for(point, regions)
        kind = access_kind(feature)
        totals[region][f"newBridge{kind.title()}"] += 1
        result["newBridgeAccess"].append({
            "id": object_id, "region": region, "kind": kind,
            "name": feature.get("properties", {}).get("name"),
            "coordinate": [round(point[1], 7), round(point[0], 7)],
        })
    result["regions"] = {region: dict(values) for region, values in sorted(totals.items())}
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
