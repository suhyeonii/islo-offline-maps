#!/usr/bin/env python3
import argparse
import sqlite3
import sys
import re


def decode_opl(value: str) -> str:
    return re.sub(r"%([0-9A-Fa-f]+)%", lambda match: chr(int(match.group(1), 16)), value)


def fields(line: str) -> dict[str, str]:
    result = {}
    for field in line.rstrip().split(" "):
        if len(field) > 1:
            result[field[0]] = field[1:]
    return result


def tags(raw: str) -> dict[str, str]:
    result = {}
    for item in raw.split(",") if raw else []:
        if "=" in item:
            key, value = item.split("=", 1)
            result[decode_opl(key)] = decode_opl(value)
    return result


parser = argparse.ArgumentParser()
parser.add_argument("output")
args = parser.parse_args()
coordinates: dict[int, tuple[float, float]] = {}
places = []
accepted = {"amenity", "shop", "tourism", "leisure", "place", "building",
            "office", "healthcare", "public_transport", "railway"}

for line in sys.stdin:
    if not line or line[0] not in {"n", "w"}:
        continue
    data = fields(line)
    osm_id = int(line.split(" ", 1)[0][1:])
    item_tags = tags(data.get("T", ""))
    if line[0] == "n":
        if "x" not in data or "y" not in data:
            continue
        coordinate = (float(data["y"]), float(data["x"]))
        coordinates[osm_id] = coordinate
    else:
        refs = [int(value[1:]) for value in data.get("N", "").split(",") if value.startswith("n")]
        points = [coordinates[ref] for ref in refs if ref in coordinates]
        if not points:
            continue
        coordinate = (
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
        )
    name = item_tags.get("name") or item_tags.get("name:ko")
    if not name or not accepted.intersection(item_tags):
        continue
    address = " ".join(filter(None, [item_tags.get("addr:city"), item_tags.get("addr:district"),
                                      item_tags.get("addr:street"), item_tags.get("addr:housenumber")]))
    category = next((item_tags[key] for key in accepted if key in item_tags), "장소")
    places.append({
        "id": f"{line[0]}{osm_id}", "name": name, "subtitle": address or category,
        "latitude": coordinate[0], "longitude": coordinate[1],
        "searchTerms": " ".join(filter(None, [name, address, category, item_tags.get("brand")]))
    })

database = sqlite3.connect(args.output)
database.execute("DROP TABLE IF EXISTS places")
database.execute("CREATE VIRTUAL TABLE places USING fts5(id UNINDEXED,name,subtitle,"
                 "latitude UNINDEXED,longitude UNINDEXED,searchTerms,tokenize='unicode61')")
database.executemany(
    "INSERT INTO places(id,name,subtitle,latitude,longitude,searchTerms) VALUES(?,?,?,?,?,?)",
    [(item["id"], item["name"], item["subtitle"], item["latitude"], item["longitude"],
      item["searchTerms"]) for item in places]
)
database.commit()
database.execute("VACUUM")
database.close()
