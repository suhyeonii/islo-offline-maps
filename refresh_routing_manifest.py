#!/usr/bin/env python3
"""Refresh one optional regional SQLite routing asset in manifest.json.

Nationwide routing is CCH-only. This command intentionally cannot add the
removed nationwide SQLite asset back to a release manifest.
"""

import hashlib
import json
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path


ROOT = Path(__file__).parent
BUILD = ROOT / "build"
VERSION = "v24"


def asset(name: str) -> dict[str, object]:
    path = BUILD / name
    return {
        "file": name,
        "size": path.stat().st_size,
        "sha256": hashlib.file_digest(path.open("rb"), "sha256").hexdigest(),
    }


def preserve_transport(previous: dict[str, object] | None, refreshed: dict[str, object]) -> dict[str, object]:
    """Never carry compressed metadata across different immutable source files."""
    if previous and previous.get("file") == refreshed.get("file"):
        for key in ("downloadFile", "downloadSize", "downloadSHA256", "compression"):
            if key in previous:
                refreshed[key] = previous[key]
    return refreshed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--release",
        help="Logical data release to publish. Omit to preserve the current manifest release.",
    )
    parser.add_argument(
        "--region",
        required=True,
        help="Refresh this regional routing asset and preserve every other asset.",
    )
    parser.add_argument(
        "--regional-version",
        default=VERSION,
        help="Regional routing filename version used with --region.",
    )
    args = parser.parse_args()
    manifest_path = ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if args.release:
        manifest["release"] = args.release
    manifest["generatedAt"] = datetime.now(timezone(timedelta(hours=9))).replace(microsecond=0).isoformat()
    matched = False
    for region in manifest["regions"]:
        if region["id"] == args.region:
            region["routing"] = preserve_transport(
                region.get("routing"),
                asset(f"{args.region}.routing.{args.regional_version}.sqlite"),
            )
            matched = True
            break
    if not matched:
        raise ValueError(f"Unknown region: {args.region}")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
