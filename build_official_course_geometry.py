#!/usr/bin/env python3
"""Map an ordered official course trace to actual OSM road geometry.

This runs at data-build time, never in the iOS app.  Each source point is
matched to the road graph in sequence and consecutive matches are joined only
through real OSM edges.  A missing connection is a build error: we never emit
a synthetic straight segment.
"""
import argparse
import csv
import heapq
import json
import math
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

CELL = 0.01  # roughly one kilometre; only retain roads around the official trace


def meters(a, b):
    return math.hypot((a[0] - b[0]) * 111_320, (a[1] - b[1]) * 90_000)


def cell(point):
    return (math.floor(point[0] / CELL), math.floor(point[1] / CELL))


def source_points(csv_path, route_id):
    points = []
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if int(row["국토종주 자전거길"]) == route_id:
                points.append((float(row["위도(LINE_XP)"]), float(row["경도(LINE_YP)"])))
    if len(points) < 2:
        raise RuntimeError(f"route {route_id} has no official source points")
    return points


def gpx_points(gpx_path):
    root = ET.parse(gpx_path).getroot()
    namespace = {"gpx": "http://www.topografix.com/GPX/1/1"}
    points = [
        (float(element.attrib["lat"]), float(element.attrib["lon"]))
        for element in root.findall(".//gpx:trk/gpx:trkseg/gpx:trkpt", namespace)
    ]
    if len(points) < 2:
        raise RuntimeError("GPX contains fewer than two track points")
    return points


def retain_roads(pbf, source, corridor_cells):
    source_cells = {cell(point) for point in source}
    command = ["osmium", "export", str(pbf), "--geometry-types", "linestring", "-f", "geojsonseq"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, text=True, bufsize=1)
    roads = []
    for raw in process.stdout:
        raw = raw.strip("\x1e\n")
        if not raw:
            continue
        feature = json.loads(raw)
        coords = [(coordinate[1], coordinate[0]) for coordinate in feature["geometry"]["coordinates"]]
        if len(coords) < 2:
            continue
        nearby = False
        for point in coords:
            row, column = cell(point)
            if any(
                (row + dy, column + dx) in source_cells
                for dy in range(-corridor_cells, corridor_cells + 1)
                for dx in range(-corridor_cells, corridor_cells + 1)
            ):
                nearby = True
                break
        if nearby:
            roads.append((coords, feature.get("properties", {})))
    if process.wait() != 0:
        raise RuntimeError("osmium export failed")
    if not roads:
        raise RuntimeError("no OSM roads found along official trace")
    return roads


def build_graph(roads, route_name):
    node_ids, nodes, graph = {}, [], []

    def node(point):
        key = (round(point[0], 7), round(point[1], 7))
        if key not in node_ids:
            node_ids[key] = len(nodes)
            nodes.append(key)
            graph.append([])
        return node_ids[key]

    normalized_name = route_name.replace(" ", "")
    for coordinates, properties in roads:
        previous = node(coordinates[0])
        named_official = normalized_name in str(properties.get("name", "")).replace(" ", "") \
            or normalized_name in str(properties.get("name:ko", "")).replace(" ", "")
        multiplier = 0.42 if named_official else 1.0
        for coordinate in coordinates[1:]:
            current = node(coordinate)
            if current != previous:
                cost = max(0.1, meters(nodes[previous], nodes[current])) * multiplier
                graph[previous].append((current, cost))
                graph[current].append((previous, cost))
            previous = current
    return nodes, graph


def repair_nearby_endpoints(nodes, graph, maximum_meters=8.0):
    """Repair only tiny OSM topology gaps between dangling road endpoints.

    This is not a geometric shortcut: both ends must be actual road endpoints
    less than eight metres apart.  Larger gaps remain a hard build failure.
    """
    grid = defaultdict(list)
    endpoints = [index for index, edges in enumerate(graph) if len(edges) <= 1]
    for index in endpoints:
        grid[cell(nodes[index])].append(index)
    added = 0
    for index in endpoints:
        row, column = cell(nodes[index])
        for y in range(row - 1, row + 2):
            for x in range(column - 1, column + 2):
                for other in grid.get((y, x), ()):
                    if other <= index:
                        continue
                    distance = meters(nodes[index], nodes[other])
                    if distance <= maximum_meters:
                        graph[index].append((other, distance))
                        graph[other].append((index, distance))
                        added += 1
    return added


def nearest_index(nodes):
    grid = defaultdict(list)
    for index, point in enumerate(nodes):
        grid[cell(point)].append(index)

    def nearest(point):
        row, column = cell(point)
        results = []
        for radius in range(4):
            for y in range(row - radius, row + radius + 1):
                for x in range(column - radius, column + radius + 1):
                    for index in grid.get((y, x), ()):
                        candidate = meters(point, nodes[index])
                        results.append((candidate, index))
            if len(results) >= 16:
                return [(index, distance) for distance, index in sorted(results)[:12]]
        if results:
            return [(index, distance) for distance, index in sorted(results)[:12]]
        raise RuntimeError(f"no OSM road near {point}")
    return nearest


def components(graph):
    result = [-1] * len(graph)
    component = 0
    for start in range(len(graph)):
        if result[start] >= 0:
            continue
        result[start] = component
        stack = [start]
        while stack:
            current = stack.pop()
            for next_node, _ in graph[current]:
                if result[next_node] < 0:
                    result[next_node] = component
                    stack.append(next_node)
        component += 1
    return result


def astar(graph, nodes, source, target, maximum_cost):
    if source == target:
        return [source], 0.0
    queue = [(meters(nodes[source], nodes[target]), 0.0, source)]
    cost = {source: 0.0}
    previous = {}
    while queue:
        _, current_cost, current = heapq.heappop(queue)
        if current_cost != cost.get(current):
            continue
        if current == target:
            path = [current]
            while current in previous:
                current = previous[current]
                path.append(current)
            return list(reversed(path)), current_cost
        for neighbor, edge_cost in graph[current]:
            next_cost = current_cost + edge_cost
            if next_cost > maximum_cost or next_cost >= cost.get(neighbor, float("inf")):
                continue
            cost[neighbor] = next_cost
            previous[neighbor] = current
            heapq.heappush(queue, (next_cost + meters(nodes[neighbor], nodes[target]), next_cost, neighbor))
    raise RuntimeError("no connected OSM path between sequential source matches")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pbf", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path("official_cycle_routes.csv"))
    parser.add_argument("--gpx", type=Path)
    parser.add_argument("--route-id", type=int, required=True)
    parser.add_argument("--route-name", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--corridor-cells", type=int, default=3)
    args = parser.parse_args()

    source = gpx_points(args.gpx) if args.gpx else source_points(args.source, args.route_id)
    roads = retain_roads(args.pbf, source, max(1, args.corridor_cells))
    nodes, graph = build_graph(roads, args.route_name)
    repaired_endpoints = repair_nearby_endpoints(nodes, graph)
    nearest = nearest_index(nodes)
    candidate_matches = [nearest(point) for point in source]
    graph_components = components(graph)
    matches = [candidate_matches[0][0]]
    if matches[0][1] > 1_500:
        raise RuntimeError(f"source-to-road map match exceeds 1.5km ({matches[0][1]:.0f}m)")

    output, total = [], 0.0
    last_source = 0
    current_match = matches[0]
    for index in range(1, len(source)):
        # Source samples are dense.  Avoid a query until the matched graph node
        # changes or the trace has progressed at least 70m.
        if candidate_matches[index][0][0] == current_match[0] and meters(source[index], source[last_source]) < 70:
            continue
        direct = meters(source[last_source], source[index])
        maximum_cost = max(800, direct * 7 + 400)
        candidates = [candidate for candidate in candidate_matches[index]
                      if graph_components[candidate[0]] == graph_components[current_match[0]]]
        candidates += [candidate for candidate in candidate_matches[index] if candidate not in candidates]
        path = None
        path_cost = None
        selected = None
        selected_score = float("inf")
        for candidate in candidates:
            try:
                candidate_path, candidate_cost = astar(
                    graph, nodes, matches[-1][0], candidate[0], maximum_cost)
            except RuntimeError:
                continue
            if direct < 400 and candidate_cost > max(1_000, direct * 5):
                continue
            # Prevent a locally closest road from trapping the ordered trace
            # on a parallel carriageway.  For dense samples, require at least
            # one plausible real-road continuation toward the next sample.
            if index + 1 < len(source):
                next_direct = meters(source[index], source[index + 1])
                if next_direct < 200:
                    has_continuation = False
                    for next_candidate in candidate_matches[index + 1][:6]:
                        try:
                            _, next_cost = astar(
                                graph, nodes, candidate[0], next_candidate[0],
                                max(800, next_direct * 7 + 400))
                        except RuntimeError:
                            continue
                        if next_cost <= max(1_000, next_direct * 5):
                            has_continuation = True
                            break
                    if not has_continuation:
                        continue
            score = candidate_cost + candidate[1] * 2.0
            if score < selected_score:
                path, path_cost, selected, selected_score = candidate_path, candidate_cost, candidate, score
        if path is None:
            candidate_debug = [
                {"latitude": round(nodes[candidate[0]][0], 7), "longitude": round(nodes[candidate[0]][1], 7),
                 "snapMeters": round(candidate[1]), "component": graph_components[candidate[0]]}
                for candidate in candidate_matches[index][:5]
            ]
            raise RuntimeError(
                f"unconnected sequential matches at source indexes {last_source}->{index} "
                f"(direct={direct:.0f}m, previousSnap={matches[-1][1]:.0f}m, candidates={candidate_debug})"
            )
        if selected[1] > 1_500:
            raise RuntimeError(f"source-to-road map match exceeds 1.5km at source index {index}")
        matches.append(selected)
        current_match = selected
        for node in path:
            point = {"latitude": nodes[node][0], "longitude": nodes[node][1]}
            if not output or point != output[-1]:
                output.append(point)
        total += path_cost
        last_source = index
    if last_source != len(source) - 1:
        target = candidate_matches[-1][0]
        path, path_cost = astar(graph, nodes, matches[-1][0], target[0], 4_000)
        for node in path:
            point = {"latitude": nodes[node][0], "longitude": nodes[node][1]}
            if not output or point != output[-1]: output.append(point)
        total += path_cost
    if len(output) < 2:
        raise RuntimeError("generated geometry is empty")
    source_length = sum(meters(a, b) for a, b in zip(source, source[1:]))
    if not (source_length * .75 <= total <= source_length * 1.55):
        raise RuntimeError(f"generated route length {total:.0f}m is inconsistent with official trace {source_length:.0f}m")
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    max_snap = max(distance for _, distance in matches)
    print(json.dumps({"routeId": args.route_id, "roads": len(roads), "repairedEndpoints": repaired_endpoints, "geometryPoints": len(output),
                      "sourcePoints": len(source), "sourceLengthMeters": round(source_length),
                      "routeLengthMeters": round(total), "maximumSnapMeters": round(max_snap)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
