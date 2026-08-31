#!/usr/bin/env python3
"""Stage one atomic map/search/routing snapshot in manifest.json."""

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def identity(path: Path, *, include_file: bool = True) -> dict:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    result = {"size": path.stat().st_size, "sha256": digest}
    if include_file:
        result["file"] = path.name
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("manifest.json"))
    parser.add_argument("--build", type=Path, default=Path("build"))
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--routing-version", required=True)
    parser.add_argument("--release", required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    manifest["release"] = args.release
    manifest["generatedAt"] = datetime.now(
        timezone(timedelta(hours=9))
    ).replace(microsecond=0).isoformat()
    nationwide = args.build / f"korea.routing.{args.routing_version}.sqlite"
    manifest["nationwideRouting"] = identity(nationwide)
    manifest["searchIndexes"] = {}
    for region in manifest["regions"]:
        identifier = region["id"]
        map_path = args.build / f"{identifier}.map.{args.snapshot}.pmtiles"
        routing_path = args.build / f"{identifier}.routing.{args.routing_version}.sqlite"
        search_path = args.build / f"{identifier}.places.{args.snapshot}.sqlite"
        region["mapFile"] = map_path.name
        region.update(identity(map_path, include_file=False))
        region["mapVersion"] = args.release
        region["routing"] = identity(routing_path)
        manifest["searchIndexes"][identifier] = identity(search_path)
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"snapshot_staged release={args.release} regions={len(manifest['regions'])}")


if __name__ == "__main__":
    main()
