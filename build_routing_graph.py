#!/usr/bin/env python3
"""Build an Islo bicycle-routing SQLite graph from an osmium OPL stream."""

import argparse
import csv
import math
import os
import re
import sqlite3
import struct
import sys
import zipfile


WAY_RE = re.compile(r"^w-?\d+ .* T(.*?) N(.*)$")


class ElevationProvider:
    """Provides elevation lookups from SRTM HGT or HGT.zip files."""

    def __init__(self, dem_dir: str | None = None) -> None:
        self.dem_dir = dem_dir
        self._tiles: dict[str, bytes] = {}

    @staticmethod
    def tile_name(latitude: float, longitude: float) -> str:
        lat_prefix = "N" if latitude >= 0 else "S"
        lat_val = int(abs(math.floor(latitude)))
        lon_prefix = "E" if longitude >= 0 else "W"
        lon_val = int(abs(math.floor(longitude)))
        return f"{lat_prefix}{lat_val:02d}{lon_prefix}{lon_val:03d}"

    def _load_tile(self, name: str) -> bytes | None:
        if name in self._tiles:
            return self._tiles[name]
        if not self.dem_dir or not os.path.isdir(self.dem_dir):
            return None

        # Check raw .hgt
        raw_path = os.path.join(self.dem_dir, f"{name}.hgt")
        if os.path.isfile(raw_path):
            with open(raw_path, "rb") as f:
                data = f.read()
                self._tiles[name] = data
                return data

        # Check .hgt.zip or .SRTMGL1.hgt.zip
        for ext in (f"{name}.hgt.zip", f"{name}.SRTMGL1.hgt.zip", f"{name}.SRTMGL3.hgt.zip"):
            zip_path = os.path.join(self.dem_dir, ext)
            if os.path.isfile(zip_path):
                try:
                    with zipfile.ZipFile(zip_path) as zf:
                        for inner_name in zf.namelist():
                            if inner_name.endswith(".hgt"):
                                data = zf.read(inner_name)
                                self._tiles[name] = data
                                return data
                except Exception:
                    pass
        return None

    def get_elevation(self, latitude: float, longitude: float) -> int:
        name = self.tile_name(latitude, longitude)
        data = self._load_tile(name)
        if not data:
            return 0

        # Determine dimensions (1201 for SRTM-3 or 3601 for SRTM-1)
        size = len(data)
        if size == 3601 * 3601 * 2:
            dim = 3601
        elif size == 1201 * 1201 * 2:
            dim = 1201
        else:
            return 0

        lat_floor = math.floor(latitude)
        lon_floor = math.floor(longitude)
        # Lat goes North to South (top to bottom)
        y_float = (lat_floor + 1.0 - latitude) * (dim - 1)
        x_float = (longitude - lon_floor) * (dim - 1)

        x0 = int(math.floor(x_float))
        y0 = int(math.floor(y_float))
        x1 = min(dim - 1, x0 + 1)
        y1 = min(dim - 1, y0 + 1)

        dx = x_float - x0
        dy = y_float - y0

        def sample(x: int, y: int) -> int:
            offset = (y * dim + x) * 2
            if offset + 2 > size:
                return 0
            val = struct.unpack(">h", data[offset : offset + 2])[0]
            # -32768 indicates void / nodata
            return 0 if val <= -32768 else val

        q11 = sample(x0, y0)
        q21 = sample(x1, y0)
        q12 = sample(x0, y1)
        q22 = sample(x1, y1)

        # Bilinear interpolation
        top = q11 * (1.0 - dx) + q21 * dx
        bottom = q12 * (1.0 - dx) + q22 * dx
        ele = top * (1.0 - dy) + bottom * dy
        return int(round(ele))


def decode_opl(value: str) -> str:
    """Decode osmium OPL's %UnicodeCodePoint% escaping.

    OPL is not URL percent encoding: for example, Korean '아' is written as
    ``%c544%`` rather than UTF-8's ``%EC%95%84``.  urllib.parse.unquote turns
    that into the corrupt string '�44%', which was then persisted in amenities.
    """
    return re.sub(
        r"%([0-9A-Fa-f]+)%",
        lambda match: chr(int(match.group(1), 16)),
        value,
    )


def tags(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    if not raw:
        return result
    for item in raw.split(","):
        key, separator, value = item.partition("=")
        if separator:
            result[decode_opl(key)] = decode_opl(value)
    return result


ALLOWED_BICYCLE = {"yes", "designated", "official", "permissive"}
FORBIDDEN_ACCESS = {"no", "private"}
PAVED_SURFACES = {
    "paved", "asphalt", "concrete", "concrete:plates", "concrete:lanes",
    "paving_stones", "sett", "unhewn_cobblestone", "compacted", "fine_gravel",
}
UNSUITABLE_CITY_BICYCLE_SURFACES = {
    "dirt", "earth", "ground", "mud", "sand", "grass", "woodchips",
}


def bicycle_profile(values: dict[str, str], compact: bool) -> tuple[float, int, int] | None:
    """Return cost, bicycle-friendly flag, and dedicated-cycleway flag.

    This intentionally stays a compact, auditable policy instead of embedding
    libosrm. Access tags are resolved before road-class preferences, while
    surface/smoothness and cycleway facilities modify the traversal rate.
    """
    highway = values.get("highway", "")
    bicycle = values.get("bicycle", "")
    access = values.get("access", "")
    vehicle = values.get("vehicle", "")
    bicycle_allowed = bicycle in ALLOWED_BICYCLE
    foot = values.get("foot", "")
    surface = values.get("surface", "")
    walkable_dismount = (
        highway in {"footway", "pedestrian", "path"}
        and foot not in FORBIDDEN_ACCESS
        and access != "private"
        and not values.get("sac_scale")
        and surface not in UNSUITABLE_CITY_BICYCLE_SURFACES
    )
    pedestrian_connector = walkable_dismount or (
        foot in ALLOWED_BICYCLE
        and highway in {"footway", "pedestrian", "path", "service"}
        and access != "private"
    )
    # `motorroad=yes` denotes a road governed by motorway-like access rules.
    # Do not infer bicycle access from its physical shoulder: a mapper must
    # explicitly remove this tag before it can be considered routable.
    if values.get("motorroad") == "yes":
        return None
    if (
        # `bicycle=no`은 자전거 통행 금지입니다. 교량이나 보행로라도
        # 자전거 경로의 "최후 수단"으로 되살리지 않습니다. 실제로 자전거를
        # 끌고 통과하도록 허용한 경우에는 OSM에 bicycle=dismount 또는
        # bicycle=yes 같은 명시 태그가 있어야 합니다.
        (bicycle in FORBIDDEN_ACCESS)
        or (access in FORBIDDEN_ACCESS and not bicycle_allowed and not pedestrian_connector)
        or (vehicle in FORBIDDEN_ACCESS and not bicycle_allowed and not pedestrian_connector)
        or highway in {"motorway", "motorway_link"}
    ):
        return None
    if highway == "steps":
        # 계단은 자전거 주행 경로에서 제외합니다. 그래프 연결성을 보존해
        # 건물·공원처럼 다른 접근이 없는 목적지에만 최후 수단으로 남기되,
        # 어떤 합리적인 자전거 우회로도 이기지 못할 비용을 부여합니다.
        return (500.0, 0, 0) if foot not in FORBIDDEN_ACCESS and access != "private" else None
    if values.get("route") == "ferry":
        return (1.8, 0, 0) if bicycle_allowed else None
    if highway in {"footway", "pedestrian"}:
        # Most generic footways are sidewalks, so they must never become a normal
        # bicycle choice. Some parks, riverside entrances and public facilities,
        # however, connect a bicycle-permitted path to the only legal road access
        # through an untagged short footway. Keep those as an expensive last-resort
        # connector while continuing to exclude stairs, restricted access and
        # unsuitable/mountain-like surfaces above.
        if bicycle in {"yes", "designated", "official"}:
            return (0.72 if bicycle == "yes" else 0.52, 1, 0)
        if values.get("sac_scale") or surface in UNSUITABLE_CITY_BICYCLE_SURFACES:
            return None
        return (3.8, 0, 0)
    if highway == "path":
        is_hiking_path = (
            bool(values.get("sac_scale"))
            or bool(values.get("trail_visibility"))
            or bool(values.get("mtb:scale"))
            or values.get("informal") == "yes"
            or surface in UNSUITABLE_CITY_BICYCLE_SURFACES
        )
        # Generic OSM `path` includes everything from paved shared-use trails to
        # steep mountain footpaths. Without an affirmative bicycle tag, only
        # surfaces suitable for ordinary city bicycles belong in this router.
        if not bicycle_allowed and is_hiking_path:
            return None
        # An untagged OSM `path` is not evidence of an urban, push-bike-safe
        # connection. In Korea many mountain trails omit both `surface` and
        # `sac_scale`; accepting those created routes through hills. Retain a
        # generic path only when its surface affirmatively describes a city-bike
        # suitable way. Ordinary `footway` remains available as the expensive
        # last-metre dismount connector to parks and buildings.
        if not bicycle_allowed and surface not in PAVED_SURFACES:
            return None
    cycleway = values.get("cycleway", "")
    cycleway_left = values.get("cycleway:left", "")
    cycleway_right = values.get("cycleway:right", "")
    cycleway_both = values.get("cycleway:both", "")
    facility_values = {cycleway, cycleway_left, cycleway_right, cycleway_both}
    has_protected_facility = bool(facility_values & {"track", "separate"})
    has_bicycle_shoulder = "shoulder" in facility_values
    has_cycle_lane = bool(facility_values & {"lane", "shared_lane", "share_busway"})
    # A generic shoulder does not prove that riding is legal. Only reward it
    # when OSM also explicitly permits cycling; cycleway=shoulder above is a
    # mapped bicycle facility and is handled as protected infrastructure.
    has_permitted_shoulder = (
        bicycle_allowed
        and values.get("shoulder", "") in {"yes", "left", "right", "both"}
    )
    # Only a separately mapped cycleway (or an explicit bicycle road) is a
    # dedicated bicycle road. `bicycle=designated`, cycle lanes, shoulders and
    # a `cycleway=track` tag on a vehicle road remain bicycle-friendly: coloring
    # that vehicle-road geometry as bicycle-only would be false.
    is_dedicated_cycleway = highway == "cycleway" or values.get("bicycle_road") == "yes"
    is_bicycle_friendly = (
        is_dedicated_cycleway
        or bicycle in {"designated", "official"}
        or has_protected_facility
        or has_bicycle_shoulder
        or has_cycle_lane
    )
    if compact and not is_bicycle_friendly and not pedestrian_connector and highway not in {
        "trunk", "primary", "secondary", "tertiary", "track", "path"
    }:
        return None
    base = {
        "cycleway": 0.42, "path": 0.55, "track": 0.65,
        "pedestrian": 3.8,
        "living_street": 0.82, "residential": 0.9, "service": 1.0,
        "unclassified": 1.05, "tertiary": 1.2, "secondary": 1.65,
        "primary": 2.2, "trunk": 3.0,
    }.get(highway)
    if base is None:
        return None
    if is_dedicated_cycleway:
        # A physically independent cycleway is the safest predictable choice.
        # Give it a stronger reward than painted lanes/designated vehicle roads.
        base = min(base, 0.34)
    elif has_protected_facility:
        base = min(base, 0.52)
    elif bicycle in {"designated", "official"}:
        base = min(base, 0.60)
    elif has_bicycle_shoulder:
        base *= 0.72
    elif has_cycle_lane:
        base *= 0.78
    elif has_permitted_shoulder:
        base *= 0.88

    smoothness = values.get("smoothness", "")
    if surface in {"cobblestone", "cobblestone:flattened", "gravel", "pebblestone"}:
        base *= 1.35
    elif surface in UNSUITABLE_CITY_BICYCLE_SURFACES:
        base *= 1.8
    if smoothness in {"bad", "very_bad"}:
        base *= 1.35
    elif smoothness in {"horrible", "very_horrible", "impassable"}:
        return None

    # Dismount sections remain usable as short connectors but must never beat a
    # normal rideable road or a mapped cycleway.
    if bicycle == "dismount":
        base = max(base, 2.4)
    # `access=no, foot=yes` is common on public park approaches and pedestrian
    # bridges: cars are prohibited, not people. It is valid only as a short
    # push-bike connector and must never compete with a rideable road.
    if pedestrian_connector and not bicycle_allowed:
        base = max(base, 4.2)
    return base, int(is_bicycle_friendly), int(is_dedicated_cycleway)


def penalty(values: dict[str, str], compact: bool) -> float | None:
    profile = bicycle_profile(values, compact)
    return profile[0] if profile else None


def crossing_wait_seconds(crossing_tags: dict[str, str] | None, way_tags: dict[str, str]) -> int:
    """Expected wait for one route crossing, based on the crossed road class.

    We intentionally use an expected wait rather than a worst-case signal
    cycle: 45 s for small streets, 75 s for ordinary 4–6 lane city roads, and
    135 s for large intersections. OSM does not consistently provide lane
    counts, so highway class is the fallback.
    """
    if not crossing_tags:
        return 0

    def lane_count(value: str) -> int:
        try:
            return max(0, int(value.split(";")[0]))
        except (AttributeError, ValueError):
            return 0

    lanes = max(
        lane_count(way_tags.get("lanes", "")),
        lane_count(way_tags.get("lanes:forward", ""))
            + lane_count(way_tags.get("lanes:backward", "")),
    )
    highway = way_tags.get("highway", "")
    if lanes >= 8 or highway in {"trunk", "primary"}:
        return 135
    if lanes >= 4 or highway in {"secondary", "tertiary"}:
        return 75
    # A signalled crossing whose road class is missing is usually not a quiet
    # two-lane street, so use the ordinary-city expectation.
    if crossing_tags.get("crossing") == "traffic_signals" \
            or crossing_tags.get("traffic_signals") == "yes":
        return 75
    return 45


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
    parser.add_argument(
        "--cch-input",
        action="store_true",
        help="Build the full routable base graph for CCH mmap extraction",
    )
    parser.add_argument("--official-csv")
    parser.add_argument("--dem-dir", help="Directory containing SRTM .hgt / .hgt.zip files")
    args = parser.parse_args()
    if args.compact and args.cch_input:
        parser.error("--compact and --cch-input are mutually exclusive")

    elevation_provider = ElevationProvider(args.dem_dir)

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
      CREATE TABLE nodes(id INTEGER PRIMARY KEY, lat REAL NOT NULL, lon REAL NOT NULL,
                         ele INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE edges(src INTEGER NOT NULL, dst INTEGER NOT NULL,
                         meters REAL NOT NULL, cost REAL NOT NULL,
                         is_cycleway INTEGER NOT NULL DEFAULT 0,
                         is_dedicated_cycleway INTEGER NOT NULL DEFAULT 0,
                         is_dismount INTEGER NOT NULL DEFAULT 0,
                         interruption_kind INTEGER NOT NULL DEFAULT 0,
                         interruption_name TEXT,
                         is_bridge INTEGER NOT NULL DEFAULT 0,
                         bridge_name TEXT,
                         crossing_wait_seconds INTEGER NOT NULL DEFAULT 0,
                         is_roundabout INTEGER NOT NULL DEFAULT 0,
                         road_name TEXT,
                         PRIMARY KEY(src, dst)) WITHOUT ROWID;
      CREATE TABLE amenities(node_id INTEGER PRIMARY KEY, kind TEXT NOT NULL,
                             name TEXT, lat REAL NOT NULL, lon REAL NOT NULL);
      CREATE TABLE official_routes(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
      CREATE TABLE official_nodes(node_id INTEGER NOT NULL, route_id INTEGER NOT NULL,
                                  sequence INTEGER NOT NULL,
                                  PRIMARY KEY(node_id, route_id)) WITHOUT ROWID;
    """)
    database.execute("INSERT INTO metadata VALUES('schemaVersion','14')")
    graph_kind = "cch-input" if args.cch_input else ("compact" if args.compact else "detail")
    database.execute("INSERT INTO metadata VALUES('kind',?)", (graph_kind,))
    database.execute("INSERT INTO metadata VALUES('routingIndex','bidirectional-v1')")
    database.execute("INSERT INTO metadata VALUES('routingHierarchy','none')")
    pending_nodes: list[tuple[int, float, float, int]] = []
    pending_edges: list[tuple] = []
    pending_amenities: list[tuple[int, str, str | None, float, float]] = []
    crossing_tags_by_node: dict[int, dict[str, str]] = {}
    interruption_by_node: dict[int, tuple[int, str | None]] = {}
    did_flush_nodes = False

    for line in sys.stdin:
        if line.startswith("n"):
            fields = line.split()
            node_id = int(fields[0][1:])
            lon = next((float(value[1:]) for value in fields if value.startswith("x")), None)
            lat = next((float(value[1:]) for value in fields if value.startswith("y")), None)
            if lat is not None and lon is not None:
                raw_tags = next((value[1:] for value in fields if value.startswith("T")), "")
                node_tags = tags(raw_tags)
                if node_tags.get("highway") == "crossing" or node_tags.get("crossing"):
                    crossing_tags_by_node[node_id] = node_tags
                if node_tags.get("highway") == "elevator" or node_tags.get("elevator") == "yes":
                    node_name = node_tags.get("name") or node_tags.get("name:ko")
                    if node_name in {"엘리베이터", "승강기", "elevator", "Elevator"}:
                        node_name = None
                    interruption_by_node[node_id] = (3, node_name)
                amenity = node_tags.get("amenity")
                shop = node_tags.get("shop")
                facility_kind = amenity if amenity in {"drinking_water", "toilets"} else (
                    shop if shop in {"convenience", "supermarket"} else None
                )
                if facility_kind:
                    pending_amenities.append(
                        (node_id, facility_kind, node_tags.get("name"), lat, lon)
                    )
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
                ele = elevation_provider.get_elevation(lat, lon)
                pending_nodes.append((node_id, lat, lon, ele))
                if len(pending_nodes) >= 50_000:
                    database.executemany("INSERT OR IGNORE INTO nodes VALUES(?,?,?,?)", pending_nodes)
                    pending_nodes.clear()
        elif line.startswith("w"):
            if not did_flush_nodes:
                database.executemany("INSERT OR IGNORE INTO nodes VALUES(?,?,?,?)", pending_nodes)
                pending_nodes.clear()
                database.commit()
                did_flush_nodes = True
            match = WAY_RE.match(line.rstrip())
            if not match:
                continue
            values = tags(match.group(1))
            profile = bicycle_profile(values, args.compact)
            if profile is None:
                continue
            weight, is_cycleway, is_dedicated_cycleway = profile
            is_dismount = int(
                values.get("highway") == "steps"
                or values.get("highway") == "elevator"
                or values.get("bicycle") == "dismount"
                or (
                    values.get("highway") in {"footway", "pedestrian", "path"}
                    and values.get("bicycle") not in {"yes", "designated", "official"}
                )
                or (
                    values.get("bicycle") == "no"
                    and values.get("bridge") == "yes"
                    and values.get("highway") in {"primary", "secondary", "tertiary"}
                )
                or (
                    values.get("foot") in ALLOWED_BICYCLE
                    and values.get("highway") in {"footway", "path", "service"}
                    and values.get("bicycle") not in FORBIDDEN_ACCESS
                )
            )
            refs = [int(value.lstrip("n")) for value in match.group(2).split(",")]
            placeholders = ",".join("?" for _ in refs)
            way_coordinates = {
                node_id: (latitude, longitude)
                for node_id, latitude, longitude in database.execute(
                    f"SELECT id,lat,lon FROM nodes WHERE id IN ({placeholders})", refs
                )
            }
            bicycle_oneway = values.get("oneway:bicycle", values.get("bicycle:oneway", ""))
            general_oneway = values.get("oneway", "")
            if bicycle_oneway in {"no", "0", "false"}:
                oneway = reverse = False
            else:
                oneway = (
                    bicycle_oneway in {"yes", "1", "true"}
                    or general_oneway in {"yes", "1", "true"}
                    or values.get("junction") == "roundabout"
                )
                reverse = bicycle_oneway == "-1" or general_oneway == "-1"
            is_roundabout = values.get("junction") == "roundabout"
            for source, destination in zip(refs, refs[1:]):
                if source not in way_coordinates or destination not in way_coordinates:
                    continue
                meters = haversine(way_coordinates[source], way_coordinates[destination])
                # Charge the wait when the directed route reaches the crossing
                # node. The reverse edge is charged symmetrically at its own
                # destination node.
                forward_crossing_wait = crossing_wait_seconds(
                    crossing_tags_by_node.get(destination), values
                )
                reverse_crossing_wait = crossing_wait_seconds(
                    crossing_tags_by_node.get(source), values
                )
                edge_source, edge_destination = source, destination
                if reverse:
                    edge_source, edge_destination = destination, source
                interruption_kind = (
                    2 if values.get("highway") == "steps"
                    else 3 if values.get("highway") == "elevator"
                    or source in interruption_by_node or destination in interruption_by_node
                    else 1 if is_dismount else 0
                )
                interruption_name = values.get("name") or values.get("name:ko")
                if not interruption_name:
                    interruption_name = (
                        interruption_by_node.get(source, (0, None))[1]
                        or interruption_by_node.get(destination, (0, None))[1]
                    )
                if interruption_name in {"엘리베이터", "승강기", "계단", "elevator", "Elevator", "steps"}:
                    interruption_name = None
                bridge_name = (
                    values.get("bridge:name:ko") or values.get("bridge:name")
                    or values.get("name:ko") or values.get("name")
                    if values.get("bridge") == "yes" else None
                )
                road_name = values.get("name:ko") or values.get("name")
                pending_edges.append(
                    (edge_source, edge_destination, meters, meters * weight, is_cycleway, is_dedicated_cycleway, is_dismount, interruption_kind, interruption_name, int(values.get("bridge") == "yes"), bridge_name,
                     reverse_crossing_wait if reverse else forward_crossing_wait, int(is_roundabout), road_name)
                )
                if not oneway and not reverse:
                    pending_edges.append((destination, source, meters, meters * weight, is_cycleway, is_dedicated_cycleway, is_dismount, interruption_kind, interruption_name, int(values.get("bridge") == "yes"), bridge_name,
                                          reverse_crossing_wait, int(is_roundabout), road_name))
                if len(pending_edges) >= 100_000:
                    database.executemany("INSERT OR REPLACE INTO edges VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", pending_edges)
                    pending_edges.clear()

    database.executemany("INSERT OR IGNORE INTO nodes VALUES(?,?,?,?)", pending_nodes)
    database.executemany("INSERT OR REPLACE INTO edges VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", pending_edges)
    database.executemany("INSERT OR REPLACE INTO amenities VALUES(?,?,?,?,?)", pending_amenities)
    if args.cch_input:
        # CCH owns its node order, adjacency and spatial index. Reverse-edge
        # SQLite indexes would duplicate data discarded after mmap extraction.
        if args.official_csv:
            database.executemany("INSERT INTO official_routes VALUES(?,?)", route_names.items())
            official_nodes = [
                (official_nearest[index][1], point[1], point[0])
                for index, point in enumerate(official_points) if index in official_nearest
            ]
            database.executemany(
                "INSERT OR IGNORE INTO official_nodes VALUES(?,?,?)", official_nodes
            )
            database.execute("""
              UPDATE edges SET cost=cost*0.45
              WHERE src IN (SELECT node_id FROM official_nodes)
                AND dst IN (SELECT node_id FROM official_nodes)
            """)
        database.execute("INSERT OR REPLACE INTO metadata VALUES('routingHierarchy','cch-input-v1')")
        database.commit()
        database.close()
        return
    database.executescript("""
      -- Endpoint snapping constrains latitude and longitude together. A
      -- composite index prevents a dense nationwide latitude band from being
      -- scanned and filtered by longitude on every route request.
      CREATE INDEX nodes_lat_lon ON nodes(lat,lon);
      CREATE INDEX edges_cycleway ON edges(is_cycleway,src);
      -- v11 라우터는 목적지 쪽에서도 동시에 탐색합니다. WITHOUT ROWID의
      -- 기본 키는 (src,dst)라 역방향 조회에는 사용할 수 없으므로 이
      -- 인덱스를 패키지 생성 시 만들어 기기에서 전체 간선 스캔을 막습니다.
      CREATE INDEX edges_destination ON edges(dst,src);
      CREATE INDEX amenities_lat ON amenities(lat);
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
