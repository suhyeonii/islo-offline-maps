#!/usr/bin/env python3
"""Compress one manifest SQLite asset and record transport identity."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("manifest.json"))
    parser.add_argument("--build", type=Path, default=Path("build"))
    parser.add_argument("--kind", choices=("nationwide", "routing", "search"), required=True)
    parser.add_argument("--region")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if args.kind == "nationwide":
        asset = manifest["nationwideRouting"]
    elif args.kind == "search":
        asset = manifest["searchIndexes"][args.region]
    else:
        asset = next(r["routing"] for r in manifest["regions"] if r["id"] == args.region)
    source = args.build / asset["file"]
    destination = args.build / f"{asset['file']}.lzfse"
    subprocess.run(
        ["compression_tool", "-encode", "-a", "lzfse", "-i", source, "-o", destination],
        check=True,
    )
    asset.update(
        downloadFile=destination.name,
        downloadSize=destination.stat().st_size,
        downloadSHA256=sha(destination),
        compression="lzfse",
    )
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(destination)


if __name__ == "__main__":
    main()
