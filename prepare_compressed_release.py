#!/usr/bin/env python3
"""Create LZFSE transport assets and a backward-compatible release manifest.

The original SQLite files remain the installed representation.  New clients use
the optional download* fields to fetch a smaller LZFSE stream, while older
clients keep downloading the uncompressed asset named by ``file``.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compress(asset: dict, build: Path, output: Path) -> None:
    source = build / asset["file"]
    if not source.is_file():
        raise FileNotFoundError(source)
    destination = output / f"{asset['file']}.lzfse"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.stat().st_mtime < source.stat().st_mtime:
        subprocess.run(
            ["compression_tool", "-encode", "-a", "lzfse", "-i", source, "-o", destination],
            check=True,
        )
    asset.update(
        downloadFile=destination.name,
        downloadSize=destination.stat().st_size,
        downloadSHA256=sha256(destination),
        compression="lzfse",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("manifest.json"))
    parser.add_argument("--build", type=Path, default=Path("build"))
    parser.add_argument("--output", type=Path, default=Path("build/compressed-release"))
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    assets = []
    if manifest.get("nationwideRouting"):
        assets.append(manifest["nationwideRouting"])
    assets.extend(manifest.get("searchIndexes", {}).values())
    assets.extend(
        region["routing"] for region in manifest.get("regions", []) if region.get("routing")
    )
    for asset in assets:
        compress(asset, args.build, args.output)

    output_manifest = args.output / "manifest.json"
    output_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    original = sum(asset["size"] for asset in assets)
    transferred = sum(asset["downloadSize"] for asset in assets)
    print(f"assets={len(assets)} original={original} transfer={transferred} ratio={transferred/original:.3f}")
    print(output_manifest)


if __name__ == "__main__":
    main()
