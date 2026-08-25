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


def category_for(item_tags: dict[str, str]) -> str:
    """Return one stable, semantic category instead of depending on set order.

    `accepted` is a set, so iterating it made the selected category vary between
    builds.  Shop/amenity/tourism values carry the POI meaning; building=yes and
    similar structural tags must never replace them.
    """
    for key in ("shop", "amenity", "tourism", "leisure", "healthcare",
                "public_transport", "railway", "office", "place", "building"):
        value = item_tags.get(key)
        if value:
            return value
    return "장소"

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
    name = item_tags.get("name") or item_tags.get("name:ko") or item_tags.get("name:en")
    if not name:
        if item_tags.get("amenity") == "toilets":
            name = "화장실"
        elif item_tags.get("amenity") == "drinking_water":
            name = "식수대"
        elif item_tags.get("amenity") == "bicycle_parking":
            name = "자전거 주차장"
        elif item_tags.get("shop") == "bicycle":
            name = "자전거 수리점"
        else:
            continue
    if not accepted.intersection(item_tags):
        continue
    address = " ".join(filter(None, [item_tags.get("addr:city"), item_tags.get("addr:district"),
                                      item_tags.get("addr:street"), item_tags.get("addr:housenumber")]))
    category = category_for(item_tags)
    places.append({
        "id": f"{line[0]}{osm_id}", "name": name,
        "name_ko": item_tags.get("name:ko", ""),
        "name_en": item_tags.get("name:en", ""),
        "subtitle": address or category,
        "category": category,
        "address": address,
        "latitude": coordinate[0], "longitude": coordinate[1],
        "searchTerms": " ".join(filter(None, [
            name, item_tags.get("name:ko"), item_tags.get("name:en"),
            address, category, item_tags.get("brand")
        ]))
    })

database = sqlite3.connect(args.output)
database.execute("DROP TABLE IF EXISTS places")
database.execute("CREATE VIRTUAL TABLE places USING fts5(id UNINDEXED,name,name_ko,name_en,subtitle,"
                 "category,address,latitude UNINDEXED,longitude UNINDEXED,searchTerms,tokenize='unicode61')")
database.executemany(
    "INSERT INTO places(id,name,name_ko,name_en,subtitle,category,address,latitude,longitude,searchTerms) "
    "VALUES(?,?,?,?,?,?,?,?,?,?)",
    [(item["id"], item["name"], item["name_ko"], item["name_en"], item["subtitle"],
      item["category"], item["address"], item["latitude"], item["longitude"], item["searchTerms"])
     for item in places]
)
database.commit()
database.execute("VACUUM")
database.close()
