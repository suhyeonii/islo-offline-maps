#!/usr/bin/env python3
"""Refresh PMTiles checksums after rebuilding a map-data release."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def asset(path: Path) -> dict[str, object]:
    with path.open("rb") as source:
        checksum = hashlib.file_digest(source, "sha256").hexdigest()
    return {"size": path.stat().st_size, "sha256": checksum}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", required=True, help="Logical map-data version.")
    parser.add_argument(
        "--asset-release",
        help="GitHub release that hosts assets; defaults to --release.",
    )
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--manifest", type=Path, default=ROOT / "manifest.json")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    for region in manifest["regions"]:
        path = args.build_dir / region["mapFile"]
        if not path.is_file():
            raise FileNotFoundError(f"Missing rebuilt map: {path}")
        region.update(asset(path))
        # The app compares this logical version before falling back to file
        # identity. This keeps a legitimate installed map distinct from a
        # damaged file even when release assets are hosted under a stable URL.
        region["mapVersion"] = args.release

    asset_release = args.asset_release or args.release
    manifest["release"] = args.release
    manifest["baseURL"] = (
        "https://github.com/suhyeonii/islo-offline-maps/releases/download/"
        + asset_release
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
