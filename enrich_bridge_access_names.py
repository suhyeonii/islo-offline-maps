#!/usr/bin/env python3
"""Name anonymous stairs/elevators by spatially joining them to bridge shapes."""
import argparse, json, math, re, shutil, sqlite3
from pathlib import Path


def records(path):
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            line = line.strip("\x1e\n ")
            if line:
                yield json.loads(line)


def lines(coordinates):
    if isinstance(coordinates[0], (int, float)):
        return [[coordinates]]
    if isinstance(coordinates[0][0], (int, float)):
        return [coordinates]
    result = []
    for value in coordinates:
        result.extend(lines(value))
    return result


def projected(point):
    return point[0] * 88_000, point[1] * 111_000


def segment_distance(point, first, second):
    px, py = projected(point); ax, ay = projected(first); bx, by = projected(second)
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    ratio = 0 if length2 == 0 else max(0, min(1, ((px-ax)*dx + (py-ay)*dy) / length2))
    return math.hypot(px - ax - ratio * dx, py - ay - ratio * dy)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("bridges")
    args = parser.parse_args()
    if Path(args.input).resolve() != Path(args.output).resolve():
        shutil.copy2(args.input, args.output)
    bridges = []
    for feature in records(args.bridges):
        properties = feature.get("properties", {})
        name = properties.get("name:ko") or properties.get("name") or ""
        name = re.sub(r"(대교)(?:북단|남단)교$", r"\1", name)
        if not ("대교" in name or name in {"광진교", "잠수교"}):
            continue
        geometry_lines = [line for line in lines(feature["geometry"]["coordinates"]) if len(line) > 1]
        points = [point for line in geometry_lines for point in line]
        if not points:
            continue
        center = (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))
        bridges.append((
            name, center, geometry_lines,
            (min(p[0] for p in points), min(p[1] for p in points),
             max(p[0] for p in points), max(p[1] for p in points))
        ))

    database = sqlite3.connect(args.output)
    facilities = database.execute("""
      SELECT DISTINCT n.id,n.lat,n.lon,e.interruption_kind
      FROM edges e JOIN nodes n ON n.id=e.src
      WHERE e.interruption_kind IN (2,3) AND e.interruption_name IS NULL
    """).fetchall()
    named = 0
    for node_id, latitude, longitude, kind in facilities:
        point = (longitude, latitude)
        nearest = (121.0, None)
        for bridge in bridges:
            west, south, east, north = bridge[3]
            if not (west - .0015 <= longitude <= east + .0015
                    and south - .0011 <= latitude <= north + .0011):
                continue
            distance = min(
                segment_distance(point, first, second)
                for line in bridge[2] for first, second in zip(line, line[1:])
            )
            if distance < nearest[0]:
                nearest = (distance, bridge)
        if nearest[1] is None:
            continue
        bridge_name, center, _, _ = nearest[1]
        north_south = "북단" if latitude >= center[1] else "남단"
        facility = "엘리베이터" if kind == 3 else "진입계단"
        name = f"{bridge_name} {north_south} {facility}"
        cursor = database.execute("""
          UPDATE edges SET interruption_name=?
          WHERE interruption_kind=? AND interruption_name IS NULL
            AND (src=? OR dst=?)
        """, (name, kind, node_id, node_id))
        named += max(0, cursor.rowcount)
    database.execute("UPDATE metadata SET value='10' WHERE key='schemaVersion'")
    database.commit()
    print(f"bridge_access_nodes_named={named}")
    database.close()


if __name__ == "__main__":
    main()
