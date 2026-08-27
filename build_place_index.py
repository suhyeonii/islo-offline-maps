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
way_coordinates: dict[int, tuple[float, float]] = {}
places = []
accepted = {"amenity", "shop", "tourism", "leisure", "place", "building",
            "office", "healthcare", "public_transport", "railway", "landuse", "cuisine"}
GROCERY_SHOPS = {
    "convenience", "supermarket", "grocery", "market", "general",
    "kiosk", "beverages", "confectionery", "variety_store",
}
COFFEE_BRANDS = {
    "스타벅스", "starbucks", "투썸", "twosome", "이디야", "ediya", "메가mgc", "메가커피",
    "megacoffee", "빽다방", "paik", "컴포즈", "compose", "할리스", "hollys", "커피빈",
    "coffeebean", "폴바셋", "paulbassett", "파스쿠찌", "pascucci", "엔제리너스",
    "angelinus", "탐앤탐스", "tomntoms", "매머드", "mammoth", "더벤티", "theventi",
    "감성커피", "달콤커피", "카페베네", "caffebene", "드롭탑", "droptop", "블루보틀",
    "bluebottle",
}


def looks_like_coffee_shop(item_tags: dict[str, str]) -> bool:
    if item_tags.get("amenity") == "cafe" or item_tags.get("shop") in {"coffee", "tea"}:
        return True
    if item_tags.get("cuisine") == "coffee_shop":
        return True
    value = " ".join(filter(None, [item_tags.get("name"), item_tags.get("brand")])).lower()
    return "커피" in value or "coffee" in value or any(brand in value for brand in COFFEE_BRANDS)


def category_for(item_tags: dict[str, str]) -> str:
    """Return one stable, semantic category instead of depending on set order.

    `accepted` is a set, so iterating it made the selected category vary between
    builds.  Shop/amenity/tourism values carry the POI meaning; building=yes and
    similar structural tags must never replace them.
    """
    # `shop=bicycle` covers ordinary sales and repair shops, but repair service
    # can also be mapped on another shop type and public self-service stations
    # use amenity=bicycle_repair_station. Keep all of these in one rider-facing
    # category without changing their OSM identity.
    if (
        item_tags.get("shop") == "bicycle"
        or item_tags.get("amenity") == "bicycle_repair_station"
        or item_tags.get("service:bicycle:repair") == "yes"
        or item_tags.get("service:bicycle:retail") == "yes"
    ):
        return "bicycle"
    # 라이딩 중 음료·간식과 기본 식료품을 살 수 있는 가게는 OSM에서 다양한
    # shop 값으로 표현됩니다. 모든 값을 하나의 강조 카테고리로 정규화하되,
    # 원본 osm_type + osm_id는 그대로 보존합니다.
    if item_tags.get("shop") in GROCERY_SHOPS:
        return "grocery"
    if looks_like_coffee_shop(item_tags):
        return "cafe"
    for key in ("shop", "amenity", "tourism", "leisure", "healthcare",
                "public_transport", "railway", "office", "place", "landuse", "building"):
        value = item_tags.get(key)
        if value:
            return value
    return "장소"

for line in sys.stdin:
    if not line or line[0] not in {"n", "w", "r"}:
        continue
    data = fields(line)
    osm_id = int(line.split(" ", 1)[0][1:])
    item_tags = tags(data.get("T", ""))
    if line[0] == "n":
        if "x" not in data or "y" not in data:
            continue
        coordinate = (float(data["y"]), float(data["x"]))
        coordinates[osm_id] = coordinate
    elif line[0] == "w":
        refs = [int(value[1:]) for value in data.get("N", "").split(",") if value.startswith("n")]
        points = [coordinates[ref] for ref in refs if ref in coordinates]
        if not points:
            continue
        coordinate = (
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
        )
        way_coordinates[osm_id] = coordinate
    else:
        refs = []
        for value in data.get("M", "").split(","):
            member = value.split("@", 1)[0]
            if member.startswith("w") and member[1:].isdigit():
                refs.append(int(member[1:]))
        points = [way_coordinates[ref] for ref in refs if ref in way_coordinates]
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
        elif (
            item_tags.get("shop") == "bicycle"
            or item_tags.get("amenity") == "bicycle_repair_station"
            or item_tags.get("service:bicycle:repair") == "yes"
            or item_tags.get("service:bicycle:retail") == "yes"
        ):
            name = "자전거 수리·판매점"
        elif item_tags.get("shop") in GROCERY_SHOPS:
            name = "식료품점"
        elif looks_like_coffee_shop(item_tags):
            name = "카페"
        else:
            continue
    if not accepted.intersection(item_tags):
        continue
    address = " ".join(filter(None, [item_tags.get("addr:city"), item_tags.get("addr:district"),
                                      item_tags.get("addr:street"), item_tags.get("addr:housenumber")]))
    category = category_for(item_tags)
    places.append({
        "id": f"{line[0]}{osm_id}",
        "osm_type": {"n": "node", "w": "way", "r": "relation"}[line[0]],
        "osm_id": osm_id,
        "name": name,
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
database.execute("CREATE VIRTUAL TABLE places USING fts5(id UNINDEXED,osm_type UNINDEXED,osm_id UNINDEXED,name,name_ko,name_en,subtitle,"
                 "category,address,latitude UNINDEXED,longitude UNINDEXED,searchTerms,tokenize='unicode61')")
database.executemany(
    "INSERT INTO places(id,osm_type,osm_id,name,name_ko,name_en,subtitle,category,address,latitude,longitude,searchTerms) "
    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
    [(item["id"], item["osm_type"], item["osm_id"], item["name"], item["name_ko"], item["name_en"], item["subtitle"],
      item["category"], item["address"], item["latitude"], item["longitude"], item["searchTerms"])
     for item in places]
)
database.commit()
database.execute("VACUUM")
database.close()
