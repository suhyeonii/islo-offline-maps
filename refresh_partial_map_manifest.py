#!/usr/bin/env python3
"""Atomically point an affected, overlap-complete region set at new PMTiles."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def identity(path: Path) -> dict[str, object]:
    with path.open("rb") as source:
        checksum = hashlib.file_digest(source, "sha256").hexdigest()
    return {"size": path.stat().st_size, "sha256": checksum}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", required=True)
    parser.add_argument("--asset-release", required=True)
    parser.add_argument("--filename-version", required=True)
    parser.add_argument("--regions", required=True)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--manifest", type=Path, default=ROOT / "manifest.json")
    args = parser.parse_args()

    selected = {value for value in args.regions.strip(",").split(",") if value}
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    known = {region["id"] for region in manifest["regions"]}
    unknown = selected - known
    if not selected or unknown:
        raise ValueError(f"Invalid region set: empty={not selected}, unknown={sorted(unknown)}")

    for region in manifest["regions"]:
        if region["id"] not in selected:
            continue
        filename = f"{region['id']}.map.{args.filename_version}.pmtiles"
        path = args.build_dir / filename
        if not path.is_file():
            raise FileNotFoundError(f"Missing rebuilt overlap asset: {path}")
        region["mapFile"] = filename
        region.update(identity(path))
        region["mapVersion"] = args.release

    manifest["release"] = args.release
    manifest["baseURL"] = (
        "https://github.com/suhyeonii/islo-offline-maps/releases/download/"
        + args.asset_release
    )
    manifest["generatedAt"] = datetime.now(
        timezone(timedelta(hours=9))
    ).replace(microsecond=0).isoformat()
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
