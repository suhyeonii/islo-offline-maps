#!/usr/bin/env python3
"""Promote routing packages to a version with a two-dimensional snap index.

The routing graph and its expensive preprocessing output are immutable during this
migration. Only the endpoint spatial index changes, so rebuilding the graph from
OSM would add risk and take hours without changing route results.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path


def migrate(source: Path, destination: Path) -> None:
    if destination.exists():
        print(f"skip existing {destination}", flush=True)
        return
    if not source.is_file():
        raise FileNotFoundError(source)

    os.replace(source, destination)
    try:
        database = sqlite3.connect(destination)
        database.execute("PRAGMA journal_mode=DELETE")
        database.execute("PRAGMA synchronous=FULL")
        database.execute("DROP INDEX IF EXISTS nodes_lat")
        database.execute("CREATE INDEX IF NOT EXISTS nodes_lat_lon ON nodes(lat,lon)")
        database.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES('routingSpatialIndex','lat-lon-v1')"
        )
        database.execute("ANALYZE nodes")
        database.commit()
        result = database.execute("PRAGMA quick_check").fetchone()
        index = database.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='nodes_lat_lon'"
        ).fetchone()
        database.close()
        if result != ("ok",) or index is None:
            raise RuntimeError(f"verification failed quick_check={result} index={index}")
    except BaseException:
        os.replace(destination, source)
        raise
    print(f"migrated {source.name} -> {destination.name}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", type=Path, default=Path("build"))
    parser.add_argument("--manifest", type=Path, default=Path("manifest.json"))
    parser.add_argument("--version", default="v23")
    args = parser.parse_args()

    import json

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    assets = [manifest["nationwideRouting"]]
    assets.extend(region["routing"] for region in manifest["regions"] if region.get("routing"))
    for asset in assets:
        source = args.build / asset["file"]
        prefix = asset["file"].split(".routing.", 1)[0]
        destination = args.build / f"{prefix}.routing.{args.version}.sqlite"
        migrate(source, destination)


if __name__ == "__main__":
    main()
