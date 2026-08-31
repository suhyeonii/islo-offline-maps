#!/usr/bin/env python3
"""Measure Islo offline-data storage before the CCH migration.

The audit deliberately hashes only equal-sized files. Different sizes cannot be
byte-for-byte duplicates, and avoiding unnecessary hashing keeps the 20+ GB
workspace audit practical. The JSON report is a release gate input, not a
human-maintained manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def category(path: Path) -> str:
    name = path.name
    if ".routing." in name:
        return "routing"
    if ".map." in name or name.endswith(".pmtiles"):
        return "map"
    if ".places." in name or name.endswith(".places.sqlite"):
        return "places"
    if name.endswith(".osm.pbf"):
        return "osm_source"
    return "other"


def referenced_assets(manifest: dict, build: Path) -> set[str]:
    references: set[str] = set()
    nationwide = manifest.get("nationwideRouting", {})
    for key in ("file", "downloadFile"):
        if nationwide.get(key):
            references.add(nationwide[key])
    for search in manifest.get("searchIndexes", {}).values():
        for key in ("file", "downloadFile"):
            if search.get(key):
                references.add(search[key])
    for region in manifest.get("regions", []):
        for key in ("mapFile", "placesFile"):
            if region.get(key):
                references.add(region[key])
        routing = region.get("routing", {})
        for key in ("file", "downloadFile"):
            if routing.get(key):
                references.add(routing[key])
    cch = manifest.get("nationwideCCH", {})
    cch_manifest = cch.get("manifest", {})
    if cch_manifest.get("file"):
        references.add(cch_manifest["file"])
        package_path = build / cch_manifest["file"]
        if package_path.is_file():
            package = json.loads(package_path.read_text(encoding="utf-8"))
            references.update(entry["file"] for entry in package.get("files", []))
    return references


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", type=Path, default=Path("build"))
    parser.add_argument("--manifest", type=Path, default=Path("manifest.json"))
    parser.add_argument("--output", type=Path, default=Path("build/cch-audit/storage-baseline.json"))
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text())
    references = referenced_assets(manifest, args.build)
    files = [
        path for path in args.build.iterdir()
        if path.is_file() and path.name not in {args.output.name, "cleanup-report.tsv"}
    ]
    sizes: dict[int, list[Path]] = defaultdict(list)
    totals: dict[str, int] = defaultdict(int)
    records = []
    for path in files:
        size = path.stat().st_size
        sizes[size].append(path)
        totals[category(path)] += size
        records.append({
            "file": path.name,
            "bytes": size,
            "category": category(path),
            "manifestReferenced": path.name in references,
        })

    duplicates = []
    for size, paths in sizes.items():
        if size == 0 or len(paths) < 2:
            continue
        by_hash: dict[str, list[str]] = defaultdict(list)
        for path in paths:
            by_hash[sha256(path)].append(path.name)
        for digest, names in by_hash.items():
            if len(names) > 1:
                duplicates.append({
                    "sha256": digest,
                    "bytesPerFile": size,
                    "files": sorted(names),
                    "reclaimableBytes": size * (len(names) - 1),
                })

    local_names = {path.name for path in files}
    report = {
        "totalBytes": sum(totals.values()),
        "categoryBytes": dict(sorted(totals.items())),
        "fileCount": len(files),
        "exactDuplicateGroups": sorted(
            duplicates, key=lambda item: item["reclaimableBytes"], reverse=True
        ),
        "exactDuplicateReclaimableBytes": sum(
            item["reclaimableBytes"] for item in duplicates
        ),
        "manifestReferencedMissingLocally": sorted(references - local_names),
        "unreferencedBuildFiles": sorted(local_names - references),
        "files": sorted(records, key=lambda item: item["bytes"], reverse=True),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in (
        "totalBytes", "categoryBytes", "fileCount",
        "exactDuplicateReclaimableBytes", "manifestReferencedMissingLocally"
    )}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
