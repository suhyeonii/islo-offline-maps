#!/usr/bin/env python3
"""Rebuild one bundled national-course polyline from its official source line.

The challenge preview must never bridge a missing route segment with a straight
line.  This tool keeps the source order, verifies every consecutive source
point, and fails the build instead of writing a visually plausible shortcut.
"""

import argparse
import csv
import json
import math
from pathlib import Path


def distance_meters(first: dict, second: dict) -> float:
    radius = 6_371_000.0
    latitude_a = math.radians(first["latitude"])
    longitude_a = math.radians(first["longitude"])
    latitude_b = math.radians(second["latitude"])
    longitude_b = math.radians(second["longitude"])
    value = math.sin((latitude_b - latitude_a) / 2) ** 2
    value += math.cos(latitude_a) * math.cos(latitude_b) * math.sin(
        (longitude_b - longitude_a) / 2
    ) ** 2
    return radius * 2 * math.asin(math.sqrt(value))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--route-id", type=int, required=True)
    parser.add_argument("--source", type=Path, default=Path("official_cycle_routes.csv"))
    parser.add_argument(
        "--output", type=Path,
        default=Path("../IsloApp/Resources/NationalCyclingRoutes.json"),
    )
    parser.add_argument("--maximum-source-gap-meters", type=float, default=250.0)
    args = parser.parse_args()

    points = []
    with args.source.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            if int(row["국토종주 자전거길"]) != args.route_id:
                continue
            points.append({
                "latitude": float(row["위도(LINE_XP)"]),
                "longitude": float(row["경도(LINE_YP)"]),
            })
    if len(points) < 2:
        raise SystemExit(f"No official points for route {args.route_id}")

    gaps = [distance_meters(first, second) for first, second in zip(points, points[1:])]
    maximum_gap = max(gaps)
    if maximum_gap > args.maximum_source_gap_meters:
        raise SystemExit(
            f"Official source has an unverified {maximum_gap:.1f}m gap "
            f"(limit={args.maximum_source_gap_meters:.1f}m)"
        )

    routes = json.loads(args.output.read_text(encoding="utf-8"))
    route = next((item for item in routes if item["id"] == args.route_id), None)
    if route is None:
        raise SystemExit(f"Missing bundled route {args.route_id}")
    route["points"] = points
    route["distanceMeters"] = sum(gaps)
    route["failed"] = []
    args.output.write_text(
        json.dumps(routes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"route={args.route_id} points={len(points)} "
        f"distanceMeters={route['distanceMeters']:.1f} maxGapMeters={maximum_gap:.1f}"
    )


if __name__ == "__main__":
    main()
