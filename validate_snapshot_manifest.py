#!/usr/bin/env python3
"""Fail publication unless every manifest asset exists and matches its digest."""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify(asset: dict, build: Path, sqlite: bool = False) -> None:
    path = build / asset["file"]
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.stat().st_size != asset["size"] or digest(path) != asset["sha256"]:
        raise ValueError(f"Asset identity mismatch: {path}")
    if sqlite:
        database = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            result = database.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            database.close()
        if result != "ok":
            raise ValueError(f"SQLite quick_check failed: {path}: {result}")


def verify_transfer(asset: dict, transfer_build: Path) -> None:
    filename = asset.get("downloadFile")
    if not filename:
        return
    path = transfer_build / filename
    if not path.is_file():
        raise FileNotFoundError(path)
    if (path.stat().st_size != asset["downloadSize"]
            or digest(path) != asset["downloadSHA256"]):
        raise ValueError(f"Transfer asset identity mismatch: {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("manifest.json"))
    parser.add_argument("--build", type=Path, default=Path("build"))
    parser.add_argument(
        "--cch-build", type=Path, default=None,
        help="Directory containing the unpacked CCH manifest and mmap assets",
    )
    parser.add_argument(
        "--transfer-build", type=Path, default=None,
        help="Directory containing compressed transport assets",
    )
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    transfer_build = args.transfer_build or args.build
    if manifest.get("nationwideRouting"):
        verify(manifest["nationwideRouting"], args.build, sqlite=True)
        verify_transfer(manifest["nationwideRouting"], transfer_build)
    for asset in manifest.get("searchIndexes", {}).values():
        verify(asset, args.build, sqlite=True)
        verify_transfer(asset, transfer_build)
    for region in manifest["regions"]:
        map_path = args.build / region["mapFile"]
        if not map_path.is_file():
            raise FileNotFoundError(map_path)
        if map_path.stat().st_size != region["size"] or digest(map_path) != region["sha256"]:
            raise ValueError(f"Map identity mismatch: {map_path}")
        if region.get("routing"):
            verify(region["routing"], args.build, sqlite=True)
            verify_transfer(region["routing"], transfer_build)

    cch = manifest.get("nationwideCCH")
    cch_file_count = 0
    if cch:
        cch_build = args.cch_build or args.build
        verify(cch["manifest"], cch_build)
        package_path = cch_build / cch["manifest"]["file"]
        package = json.loads(package_path.read_text(encoding="utf-8"))
        if package.get("schema") != "islo-cch-mmap-v2":
            raise ValueError(f"Unsupported CCH schema: {package.get('schema')}")
        if package.get("cchPrefix") != cch["version"]:
            raise ValueError(
                f"CCH version mismatch: {package.get('cchPrefix')} != {cch['version']}"
            )
        entries = package.get("files", [])
        if not entries:
            raise ValueError("CCH package contains no files")
        total = 0
        for entry in entries:
            asset = {
                "file": entry["file"],
                "size": entry["bytes"],
                "sha256": entry["sha256"],
            }
            verify(asset, cch_build)
            total += entry["bytes"]
        if total != cch["installedSize"] or total != package.get("totalBytes"):
            raise ValueError(
                f"CCH installed size mismatch: files={total} "
                f"distribution={cch['installedSize']} package={package.get('totalBytes')}"
            )
        cch_file_count = len(entries)
    print(
        f"snapshot_valid release={manifest['release']} "
        f"regions={len(manifest['regions'])} cchFiles={cch_file_count}"
    )


if __name__ == "__main__":
    main()
