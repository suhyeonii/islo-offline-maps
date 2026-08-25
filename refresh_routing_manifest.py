#!/usr/bin/env python3
"""Refresh every routing asset in manifest.json from a generated version."""

import hashlib
import json
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path


ROOT = Path(__file__).parent
BUILD = ROOT / "build"
VERSION = "v10"


def asset(name: str) -> dict[str, object]:
    path = BUILD / name
    return {
        "file": name,
        "size": path.stat().st_size,
        "sha256": hashlib.file_digest(path.open("rb"), "sha256").hexdigest(),
    }


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
    manifest["nationwideRouting"] = asset(f"korea.routing.{VERSION}.sqlite")
    for region in manifest["regions"]:
        region["routing"] = asset(f"{region['id']}.routing.{VERSION}.sqlite")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
