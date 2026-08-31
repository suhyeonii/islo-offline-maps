#!/usr/bin/env python3
import argparse
import hashlib
import json
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(8 * 1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Write a fail-closed CCH mmap file manifest")
    parser.add_argument("prefix", type=Path, nargs="+")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--graph-prefix", required=True)
    parser.add_argument("--cch-prefix", required=True)
    parser.add_argument("--index-prefix", required=True)
    args = parser.parse_args()

    files = sorted({
        path
        for prefix in args.prefix
        for path in prefix.parent.glob(prefix.name + ".*")
        if path != args.output and path != args.report
    })
    if not files:
        raise SystemExit("no CCH files matched the output prefix")
    entries = []
    for path in files:
        entries.append({"file": path.name, "bytes": path.stat().st_size, "sha256": digest(path)})
    report = json.loads(args.report.read_text())
    manifest = {
        "schema": "islo-cch-mmap-v2",
        "graphPrefix": args.graph_prefix,
        "cchPrefix": args.cch_prefix,
        "indexPrefix": args.index_prefix,
        "nodeCount": report["activeNodes"],
        "inputArcCount": report["inputArcs"],
        "cchArcCount": report["cchArcs"],
        "files": entries,
        "totalBytes": sum(entry["bytes"] for entry in entries),
    }
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"Wrote {args.output} with {len(entries)} files and {manifest['totalBytes']} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
