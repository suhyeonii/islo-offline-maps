#!/usr/bin/env python3
"""Restore complete OSM way geometry omitted by a matched GPX export.

GraphHopper's GPX writer may retain only routing/instruction points on a long
OSM way.  Drawing those points directly produces visible chords.  This build
step finds every consecutive matched pair on its source OSM way and restores
all intermediate shape points.  It fails rather than emitting a long chord
that cannot be proven to come from one OSM way.
"""

import argparse
import json
import math
import subprocess
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


def meters(left, right):
    return math.hypot(
        (left[0] - right[0]) * 111_320,
        (left[1] - right[1]) * 90_000,
    )


def coordinate_key(point):
    return round(point[0], 6), round(point[1], 6)


def read_gpx(path):
    root = ET.parse(path).getroot()
    namespace = {"gpx": "http://www.topografix.com/GPX/1/1"}
    points = [
        (float(element.attrib["lat"]), float(element.attrib["lon"]))
        for element in root.findall(".//gpx:trk/gpx:trkseg/gpx:trkpt", namespace)
    ]
    if len(points) < 2:
        raise RuntimeError("matched GPX contains fewer than two points")
    return points


def path_length(points):
    return sum(meters(left, right) for left, right in zip(points, points[1:]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpx", type=Path, required=True)
    parser.add_argument("--pbf", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--restore-over-meters", type=float, default=30.0)
    args = parser.parse_args()

    matched = read_gpx(args.gpx)
    targets = {}
    targets_by_key = defaultdict(set)
    for index, (left, right) in enumerate(zip(matched, matched[1:])):
        if meters(left, right) <= args.restore_over_meters:
            continue
        left_key = coordinate_key(left)
        right_key = coordinate_key(right)
        targets[index] = (left_key, right_key)
        targets_by_key[left_key].add(index)
        targets_by_key[right_key].add(index)

    candidates = defaultdict(list)
    command = [
        "osmium", "export", str(args.pbf),
        "--geometry-types", "linestring",
        "-f", "geojsonseq",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, text=True, bufsize=1)
    assert process.stdout is not None
    for raw_line in process.stdout:
        raw_line = raw_line.strip("\x1e\n")
        if not raw_line:
            continue
        feature = json.loads(raw_line)
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "LineString":
            continue
        coordinates = [
            (float(coordinate[1]), float(coordinate[0]))
            for coordinate in geometry.get("coordinates", [])
        ]
        if len(coordinates) < 2:
            continue
        positions = defaultdict(list)
        relevant = set()
        for position, coordinate in enumerate(coordinates):
            key = coordinate_key(coordinate)
            if key in targets_by_key:
                positions[key].append(position)
                relevant.update(targets_by_key[key])
        for target_index in relevant:
            left_key, right_key = targets[target_index]
            if left_key not in positions or right_key not in positions:
                continue
            for left_position in positions[left_key]:
                for right_position in positions[right_key]:
                    low = min(left_position, right_position)
                    high = max(left_position, right_position)
                    restored = coordinates[low:high + 1]
                    if left_position > right_position:
                        restored.reverse()
                    direct = meters(matched[target_index], matched[target_index + 1])
                    restored_length = path_length(restored)
                    if restored_length <= max(100.0, direct * 4.0):
                        candidates[target_index].append((restored_length, restored))
    if process.wait() != 0:
        raise RuntimeError("osmium export failed")

    unresolved = [index for index in targets if index not in candidates]
    if unresolved:
        preview = [
            {
                "index": index,
                "distanceMeters": round(meters(matched[index], matched[index + 1])),
                "from": matched[index],
                "to": matched[index + 1],
            }
            for index in unresolved[:20]
        ]
        raise RuntimeError(
            f"cannot restore {len(unresolved)} long matched edges from OSM ways: {preview}"
        )

    output = [matched[0]]
    restored_edges = 0
    for index in range(len(matched) - 1):
        if index in candidates:
            restored = min(candidates[index], key=lambda item: item[0])[1]
            output.extend(restored[1:])
            restored_edges += 1
        else:
            output.append(matched[index + 1])

    maximum_gap = max(meters(left, right) for left, right in zip(output, output[1:]))
    if maximum_gap > args.restore_over_meters + 1:
        raise RuntimeError(f"restored geometry still contains a {maximum_gap:.1f}m chord")

    payload = [
        {"latitude": latitude, "longitude": longitude}
        for latitude, longitude in output
    ]
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "matchedPoints": len(matched),
        "restoredPoints": len(output),
        "restoredEdges": restored_edges,
        "distanceMeters": round(path_length(output)),
        "maximumAdjacentMeters": round(maximum_gap, 1),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
