#!/usr/bin/env python3
"""Refresh regional place-search assets in manifest.json from generated v2 indexes."""

import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path


ROOT = Path(__file__).parent
BUILD = ROOT / "build"


def asset(path: Path) -> dict[str, object]:
    with path.open("rb") as source:
        checksum = hashlib.file_digest(source, "sha256").hexdigest()
    return {"file": path.name, "size": path.stat().st_size, "sha256": checksum}


def main() -> None:
    manifest_path = ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["release"] = "v0.1.0-search-v2"
    manifest["generatedAt"] = datetime.now(timezone(timedelta(hours=9))).replace(microsecond=0).isoformat()
    manifest["searchIndexes"] = {
        region["id"]: asset(BUILD / f"{region['id']}.places.v2.sqlite")
        for region in manifest["regions"]
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
