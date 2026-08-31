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
    by_name = {path.name: path for path in files}
    required_suffixes = (
        ".first_out.u32",
        ".head.u32",
        ".latitude.f32",
        ".longitude.f32",
        ".component.u32",
        ".first_geometry.u32",
        ".geometry_latitude.f32",
        ".geometry_longitude.f32",
        ".arc_geometry_reversed.u8",
        ".distance.u32",
        ".flags.u8",
        ".official_route.u8",
        ".crossing_wait.u8",
        ".elevation_gain.u16",
        ".elevation_loss.u16",
        ".arc_geometry_id.u32",
        ".arc_label_id.u32",
        ".labels.txt",
    )
    required_files = [args.graph_prefix + suffix for suffix in required_suffixes]
    required_files += [
        args.cch_prefix + suffix
        for suffix in (
            ".rank.u32",
            ".elimination_parent.u32",
            ".up_first_out.u32",
            ".up_head.u32",
            ".up_tail.u32",
            ".down_first_out.u32",
            ".down_head.u32",
            ".down_to_up.u32",
            ".forward_input_arc.u32",
            ".backward_input_arc.u32",
            ".input_arc_to_cch_arc.u32",
            ".has_input_arc.bits",
            ".is_input_arc_upward.bits",
            ".bicycle.forward.u32",
            ".bicycle.backward.u32",
            ".bicycle.input_weight.u32",
            ".shortest.forward.u32",
            ".shortest.backward.u32",
            ".shortest.input_weight.u32",
            ".flat.forward.u32",
            ".flat.backward.u32",
            ".flat.input_weight.u32",
        )
    ]
    required_files += [
        args.index_prefix + suffix
        for suffix in (
            ".first_arc_of_geometry.u32",
            ".arc_of_geometry.u32",
            ".cell_key.u64",
            ".first_geometry_of_cell.u32",
            ".geometry_of_cell.u32",
        )
    ]
    missing = sorted(name for name in required_files if name not in by_name)
    if missing:
        raise SystemExit("missing app-required CCH files: " + ", ".join(missing))
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
