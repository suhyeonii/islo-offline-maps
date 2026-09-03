#!/usr/bin/env python3
"""Add only audited bicycle-access connectors to an extracted CCH input graph.

OSM occasionally maps a marked bicycle crossing or pedestrian bridge to a
one-way carriageway but omits the short, legal approach path beside it.  The
generic router must keep respecting one-way traffic.  This tool instead adds a
reverse arc only for a reviewed, coordinate-pinned access segment in
``verified_bicycle_connectors.json``.  It fails closed when either endpoint or
the existing opposite arc no longer matches the source map.
"""

import argparse
import json
import shutil
from pathlib import Path

import numpy as np


ARC_FILES = {
    "head.u32": "<u4",
    "distance.u32": "<u4",
    "comfort.u8": "u1",
    "flags.u8": "u1",
    "road_class.u8": "u1",
    "crossing_wait.u8": "u1",
    "arc_label_id.u32": "<u4",
    "arc_geometry_id.u32": "<u4",
    "arc_geometry_reversed.u8": "u1",
    "official_route.u8": "u1",
    "elevation_gain.u16": "<u2",
    "elevation_loss.u16": "<u2",
}
NODE_OR_GEOMETRY_FILES = (
    "latitude.f32", "longitude.f32", "elevation.i16", "node_flags.u8",
    "node_label_id.u32", "labels.txt", "first_geometry.u32",
    "geometry_latitude.f32", "geometry_longitude.f32", "geometry_elevation.i16",
)


def meters_between(latitude_a, longitude_a, latitude_b, longitude_b):
    north = (latitude_a - latitude_b) * 111_132.0
    east = (longitude_a - longitude_b) * 88_300.0
    return float(np.hypot(north, east))


def nearest_node(latitude, longitude, latitudes, longitudes):
    latitude_delta = (latitudes.astype(np.float64) - latitude) * 111_132.0
    longitude_delta = (longitudes.astype(np.float64) - longitude) * 88_300.0
    index = int(np.argmin(latitude_delta * latitude_delta + longitude_delta * longitude_delta))
    return index, float(np.hypot(latitude_delta[index], longitude_delta[index]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-prefix", type=Path, required=True)
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--connectors", type=Path,
                        default=Path("cch/verified_bicycle_connectors.json"))
    args = parser.parse_args()

    source = args.source_prefix
    output = args.output_prefix
    if output.parent != source.parent:
        output.parent.mkdir(parents=True, exist_ok=True)
    if any(output.parent.glob(output.name + ".*")):
        raise SystemExit(f"refusing to overwrite existing output prefix: {output}")

    connectors = json.loads(args.connectors.read_text(encoding="utf-8"))
    first_out = np.fromfile(source.with_suffix(".first_out.u32"), dtype="<u4")
    latitudes = np.fromfile(source.with_suffix(".latitude.f32"), dtype="<f4")
    longitudes = np.fromfile(source.with_suffix(".longitude.f32"), dtype="<f4")
    heads = np.fromfile(source.with_suffix(".head.u32"), dtype="<u4")
    if len(first_out) != len(latitudes) + 1 or int(first_out[-1]) != len(heads):
        raise SystemExit("invalid source CSR graph")
    tails = np.repeat(np.arange(len(latitudes), dtype=np.uint32), np.diff(first_out))

    source_arcs = []
    for connector in connectors:
        from_node, from_error = nearest_node(
            connector["from"]["latitude"], connector["from"]["longitude"], latitudes, longitudes)
        to_node, to_error = nearest_node(
            connector["to"]["latitude"], connector["to"]["longitude"], latitudes, longitudes)
        allowed = float(connector["maxSnapMeters"])
        if from_error > allowed or to_error > allowed:
            raise SystemExit(
                f"{connector['id']}: source-map endpoint moved "
                f"(from={from_error:.1f}m to={to_error:.1f}m, max={allowed:.1f}m)"
            )
        opposite = np.flatnonzero((tails == to_node) & (heads == from_node))
        if len(opposite) != 1:
            raise SystemExit(
                f"{connector['id']}: expected exactly one existing opposite arc, found {len(opposite)}"
            )
        source_arcs.append((int(opposite[0]), from_node, to_node, connector))

    count = len(heads)
    synthetic_source = np.array([item[0] for item in source_arcs], dtype=np.uint32)
    synthetic_tail = np.array([item[1] for item in source_arcs], dtype=np.uint32)
    synthetic_head = np.array([item[2] for item in source_arcs], dtype=np.uint32)
    order = np.argsort(np.concatenate((tails, synthetic_tail)), kind="stable")
    output_tails = np.concatenate((tails, synthetic_tail))[order]
    output_first_out = np.zeros(len(latitudes) + 1, dtype=np.uint32)
    np.cumsum(np.bincount(output_tails, minlength=len(latitudes)), out=output_first_out[1:])
    output_first_out.tofile(output.with_suffix(".first_out.u32"))

    for suffix, dtype in ARC_FILES.items():
        values = np.fromfile(source.with_suffix("." + suffix), dtype=dtype)
        if len(values) != count:
            raise SystemExit(f"{suffix}: expected {count} arcs, found {len(values)}")
        appended = values[synthetic_source].copy()
        if suffix == "head.u32":
            appended = synthetic_head
        elif suffix == "flags.u8":
            # A reviewed access segment is a bicycle-friendly connector, not a
            # generic primary-road reverse edge.
            appended |= np.uint8(1)
        elif suffix == "road_class.u8":
            appended = np.maximum(appended, np.uint8(1))
        elif suffix == "crossing_wait.u8":
            appended = np.array(
                [item[3]["crossingWaitSeconds"] for item in source_arcs], dtype=np.uint8)
        elif suffix == "arc_geometry_reversed.u8":
            appended ^= np.uint8(1)
        elif suffix == "elevation_gain.u16":
            loss = np.fromfile(source.with_suffix(".elevation_loss.u16"), dtype="<u2")
            appended = loss[synthetic_source]
        elif suffix == "elevation_loss.u16":
            gain = np.fromfile(source.with_suffix(".elevation_gain.u16"), dtype="<u2")
            appended = gain[synthetic_source]
        np.concatenate((values, appended))[order].tofile(output.with_suffix("." + suffix))

    for suffix in NODE_OR_GEOMETRY_FILES:
        source_file = source.with_suffix("." + suffix)
        if source_file.exists():
            shutil.copy2(source_file, output.with_suffix("." + suffix))

    summary = {
        "sourcePrefix": str(source),
        "outputPrefix": str(output),
        "baseArcs": count,
        "injectedArcs": [
            {
                "id": item[3]["id"],
                "sourceArc": item[0],
                "fromNode": item[1],
                "toNode": item[2],
                "reason": item[3]["reason"],
            }
            for item in source_arcs
        ],
        "outputArcs": count + len(source_arcs),
    }
    output.with_suffix(".verified-connectors.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
