#!/usr/bin/env python3
"""Add DEM elevation and official bicycle-axis attributes to CCH mmap arrays."""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree


def mapped(prefix: Path, suffix: str, dtype: str) -> np.memmap:
    return np.memmap(prefix.with_name(prefix.name + suffix), dtype=dtype, mode="r")


class DEMSampler:
    def __init__(self, directory: Path):
        self.directory = directory
        self.cache: dict[tuple[int, int], np.memmap] = {}

    def tile(self, latitude: int, longitude: int) -> np.memmap:
        key = (latitude, longitude)
        if key not in self.cache:
            lat_name = f"N{latitude:02d}" if latitude >= 0 else f"S{-latitude:02d}"
            lon_name = f"E{longitude:03d}" if longitude >= 0 else f"W{-longitude:03d}"
            path = self.directory / f"{lat_name}{lon_name}.hgt"
            samples = path.stat().st_size // 2
            side = math.isqrt(samples)
            if side * side != samples:
                raise ValueError(f"invalid HGT dimensions: {path}")
            self.cache[key] = np.memmap(path, dtype=">i2", mode="r", shape=(side, side))
        return self.cache[key]

    def sample(self, latitude: np.ndarray, longitude: np.ndarray) -> np.ndarray:
        result = np.zeros(latitude.size, dtype=np.int16)
        lat_floor = np.floor(latitude).astype(np.int16)
        lon_floor = np.floor(longitude).astype(np.int16)
        keys = np.unique(np.stack((lat_floor, lon_floor), axis=1), axis=0)
        for lat, lon in keys:
            mask = (lat_floor == lat) & (lon_floor == lon)
            try:
                tile = self.tile(int(lat), int(lon))
            except FileNotFoundError:
                continue
            side = tile.shape[0]
            rows = np.rint((lat + 1 - latitude[mask]) * (side - 1)).astype(np.int32)
            columns = np.rint((longitude[mask] - lon) * (side - 1)).astype(np.int32)
            values = np.asarray(tile[rows.clip(0, side - 1), columns.clip(0, side - 1)], dtype=np.int32)
            values[(values < -500) | (values > 9_000)] = 0
            result[mask] = values.astype(np.int16)
        return result


def write_elevations(prefix: Path, dem_directory: Path, chunk_size: int) -> tuple[np.memmap, np.memmap]:
    sampler = DEMSampler(dem_directory)
    latitude = mapped(prefix, ".latitude.f32", "<f4")
    longitude = mapped(prefix, ".longitude.f32", "<f4")
    geometry_latitude = mapped(prefix, ".geometry_latitude.f32", "<f4")
    geometry_longitude = mapped(prefix, ".geometry_longitude.f32", "<f4")
    node_output = np.memmap(prefix.with_name(prefix.name + ".elevation.i16"), dtype="<i2", mode="w+", shape=latitude.shape)
    geometry_output = np.memmap(prefix.with_name(prefix.name + ".geometry_elevation.i16"), dtype="<i2", mode="w+", shape=geometry_latitude.shape)
    for begin in range(0, latitude.size, chunk_size):
        end = min(latitude.size, begin + chunk_size)
        node_output[begin:end] = sampler.sample(latitude[begin:end], longitude[begin:end])
    for begin in range(0, geometry_latitude.size, chunk_size):
        end = min(geometry_latitude.size, begin + chunk_size)
        geometry_output[begin:end] = sampler.sample(
            geometry_latitude[begin:end], geometry_longitude[begin:end])
    node_output.flush()
    geometry_output.flush()
    return node_output, geometry_output


def arc_attributes(prefix: Path, node_elevation: np.ndarray, geometry_elevation: np.ndarray) -> None:
    first_out = mapped(prefix, ".first_out.u32", "<u4")
    head = mapped(prefix, ".head.u32", "<u4")
    geometry_id = mapped(prefix, ".arc_geometry_id.u32", "<u4")
    reversed_arc = mapped(prefix, ".arc_geometry_reversed.u8", "u1")
    first_geometry = mapped(prefix, ".first_geometry.u32", "<u4")
    tail = np.repeat(np.arange(first_out.size - 1, dtype=np.uint32), np.diff(first_out))
    geometry_count = first_geometry.size - 1
    invalid = np.uint32(np.iinfo(np.uint32).max)
    representative = np.full(geometry_count, invalid, dtype=np.uint32)
    forward_ids = np.nonzero(reversed_arc == 0)[0].astype(np.uint32)
    np.minimum.at(representative, geometry_id[forward_ids], forward_ids)
    missing = representative == invalid
    if np.any(missing):
        fallback = np.full(geometry_count, invalid, dtype=np.uint32)
        all_arcs = np.arange(head.size, dtype=np.uint32)
        np.minimum.at(fallback, geometry_id, all_arcs)
        representative[missing] = fallback[missing]

    internal_gain = np.zeros(geometry_count, dtype=np.uint32)
    internal_loss = np.zeros(geometry_count, dtype=np.uint32)
    if geometry_elevation.size:
        delta = np.diff(np.asarray(geometry_elevation, dtype=np.int32))
        delta_index = np.arange(delta.size, dtype=np.int64)
        delta_geometry = np.searchsorted(first_geometry[1:], delta_index, side="right")
        valid = (delta_geometry < geometry_count) & (
            delta_index + 1 < first_geometry[np.minimum(delta_geometry + 1, geometry_count)]
        )
        internal_gain = np.bincount(
            delta_geometry[valid], weights=np.maximum(delta[valid], 0), minlength=geometry_count
        ).clip(0, np.iinfo(np.uint32).max).astype(np.uint32)
        internal_loss = np.bincount(
            delta_geometry[valid], weights=np.maximum(-delta[valid], 0), minlength=geometry_count
        ).clip(0, np.iinfo(np.uint32).max).astype(np.uint32)

    canonical_gain = internal_gain.astype(np.int64)
    canonical_loss = internal_loss.astype(np.int64)
    for geometry in range(geometry_count):
        arc = int(representative[geometry])
        start = int(node_elevation[int(tail[arc])])
        finish = int(node_elevation[int(head[arc])])
        begin = int(first_geometry[geometry])
        end = int(first_geometry[geometry + 1])
        if begin < end:
            first = int(geometry_elevation[begin])
            last = int(geometry_elevation[end - 1])
            deltas = (first - start, finish - last)
        else:
            deltas = (finish - start,)
        for delta in deltas:
            if delta > 0:
                canonical_gain[geometry] += delta
            else:
                canonical_loss[geometry] -= delta
    gain = canonical_gain[geometry_id]
    loss = canonical_loss[geometry_id]
    reverse = reversed_arc != 0
    directed_gain = np.where(reverse, loss, gain).clip(0, 65_535).astype("<u2")
    directed_loss = np.where(reverse, gain, loss).clip(0, 65_535).astype("<u2")
    directed_gain.tofile(prefix.with_name(prefix.name + ".elevation_gain.u16"))
    directed_loss.tofile(prefix.with_name(prefix.name + ".elevation_loss.u16"))


def official_route_ids(prefix: Path, csv_path: Path, maximum_distance_meters: float) -> None:
    points: list[tuple[float, float]] = []
    route_ids: list[int] = []
    with csv_path.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            try:
                route_ids.append(int(row["국토종주 자전거길"]))
                points.append((float(row["위도(LINE_XP)"]), float(row["경도(LINE_YP)"])))
            except (KeyError, TypeError, ValueError):
                continue
    latitude = mapped(prefix, ".latitude.f32", "<f4")
    longitude = mapped(prefix, ".longitude.f32", "<f4")
    first_out = mapped(prefix, ".first_out.u32", "<u4")
    head = mapped(prefix, ".head.u32", "<u4")
    geometry_id = mapped(prefix, ".arc_geometry_id.u32", "<u4")
    first_geometry = mapped(prefix, ".first_geometry.u32", "<u4")
    geometry_latitude = mapped(prefix, ".geometry_latitude.f32", "<f4")
    geometry_longitude = mapped(prefix, ".geometry_longitude.f32", "<f4")
    tail = np.repeat(np.arange(first_out.size - 1, dtype=np.uint32), np.diff(first_out))
    geometry_count = first_geometry.size - 1
    representative = np.full(geometry_count, np.iinfo(np.uint32).max, dtype=np.uint32)
    arcs = np.arange(head.size, dtype=np.uint32)
    np.minimum.at(representative, geometry_id, arcs)
    mid_lat = (latitude[tail[representative]] + latitude[head[representative]]) * 0.5
    mid_lon = (longitude[tail[representative]] + longitude[head[representative]]) * 0.5
    nonempty = first_geometry[:-1] < first_geometry[1:]
    middle_index = ((first_geometry[:-1] + first_geometry[1:] - 1) // 2).astype(np.int64)
    mid_lat[nonempty] = geometry_latitude[middle_index[nonempty]]
    mid_lon[nonempty] = geometry_longitude[middle_index[nonempty]]
    scale = math.cos(math.radians(36.0))
    tree_points = np.column_stack((np.asarray(points)[:, 0], np.asarray(points)[:, 1] * scale))
    tree = cKDTree(tree_points)
    distance, nearest = tree.query(np.column_stack((mid_lat, mid_lon * scale)), workers=-1)
    route = np.zeros(geometry_count, dtype=np.uint8)
    accepted = distance * 111_320 <= maximum_distance_meters
    route[accepted] = np.asarray(route_ids, dtype=np.uint8)[nearest[accepted]]
    route[geometry_id].tofile(prefix.with_name(prefix.name + ".official_route.u8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("graph_prefix", type=Path)
    parser.add_argument("--dem-dir", type=Path, required=True)
    parser.add_argument("--official-csv", type=Path, required=True)
    parser.add_argument("--chunk-size", type=int, default=750_000)
    parser.add_argument("--official-distance", type=float, default=45.0)
    args = parser.parse_args()
    node_elevation, geometry_elevation = write_elevations(
        args.graph_prefix, args.dem_dir, args.chunk_size)
    arc_attributes(args.graph_prefix, node_elevation, geometry_elevation)
    official_route_ids(args.graph_prefix, args.official_csv, args.official_distance)
    print(f"enriched {args.graph_prefix}")


if __name__ == "__main__":
    main()
