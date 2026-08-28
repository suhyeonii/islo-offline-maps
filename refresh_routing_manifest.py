#!/usr/bin/env python3
"""Refresh every routing asset in manifest.json from a generated version."""

import hashlib
import json
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path


ROOT = Path(__file__).parent
BUILD = ROOT / "build"
VERSION = "v14"


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
    parser.add_argument(
        "--nationwide-only",
        action="store_true",
        help="Refresh only the nationwide routing asset and preserve regional versions.",
    )
    parser.add_argument(
        "--nationwide-version",
        default=VERSION,
        help="Nationwide routing filename version (for example v15).",
    )
    args = parser.parse_args()
    manifest_path = ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if args.release:
        manifest["release"] = args.release
    manifest["generatedAt"] = datetime.now(timezone(timedelta(hours=9))).replace(microsecond=0).isoformat()
    manifest["nationwideRouting"] = asset(
        f"korea.routing.{args.nationwide_version}.sqlite"
    )
    if args.nationwide_only:
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        return
    for region in manifest["regions"]:
        region["routing"] = asset(f"{region['id']}.routing.{VERSION}.sqlite")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
