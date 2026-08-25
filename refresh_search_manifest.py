#!/usr/bin/env python3
"""Refresh regional place-search assets in manifest.json from generated v2 indexes."""

import hashlib
import json
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path


ROOT = Path(__file__).parent
BUILD = ROOT / "build"


def asset(path: Path) -> dict[str, object]:
    with path.open("rb") as source:
        checksum = hashlib.file_digest(source, "sha256").hexdigest()
    return {"file": path.name, "size": path.stat().st_size, "sha256": checksum}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--release",
        help="Logical data release to publish. Omit to preserve the current manifest release.",
    )
    args = parser.parse_args()
    manifest_path = ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if args.release:
        manifest["release"] = args.release
    manifest["generatedAt"] = datetime.now(timezone(timedelta(hours=9))).replace(microsecond=0).isoformat()
    manifest["searchIndexes"] = {
        region["id"]: asset(BUILD / f"{region['id']}.places.v2.sqlite")
        for region in manifest["regions"]
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
