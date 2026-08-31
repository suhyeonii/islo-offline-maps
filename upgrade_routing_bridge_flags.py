#!/usr/bin/env python3
"""Add OSM bridge=yes edge flags to an existing routing database.

Reads OPL from stdin so a released hierarchy can be upgraded without rebuilding
millions of preprocessed routing records when only edge presentation metadata changed.
"""
import argparse
import re
import shutil
import sqlite3
import sys

REFS_RE = re.compile(r" N([^ ]+)")
TAGS_RE = re.compile(r" T([^ ]*)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    args = parser.parse_args()
    shutil.copy2(args.input, args.output)
    database = sqlite3.connect(args.output)
    columns = {row[1] for row in database.execute("PRAGMA table_info(edges)")}
    if "is_bridge" not in columns:
        database.execute("ALTER TABLE edges ADD COLUMN is_bridge INTEGER NOT NULL DEFAULT 0")
    database.execute("UPDATE metadata SET value='9' WHERE key='schemaVersion'")
    updates: list[tuple[int, int]] = []
    for line in sys.stdin:
        refs_match = REFS_RE.search(line)
        tags_match = TAGS_RE.search(line)
        if not line.startswith("w") or not refs_match or not tags_match:
            continue
        if "bridge=yes" not in tags_match.group(1).split(","):
            continue
        refs = [int(value.lstrip("n")) for value in refs_match.group(1).split(",")]
        for source, destination in zip(refs, refs[1:]):
            updates.append((source, destination))
            updates.append((destination, source))
        if len(updates) >= 50_000:
            database.executemany(
                "UPDATE edges SET is_bridge=1 WHERE src=? AND dst=?", updates
            )
            updates.clear()
    database.executemany("UPDATE edges SET is_bridge=1 WHERE src=? AND dst=?", updates)
    database.commit()
    count = database.execute("SELECT count(*) FROM edges WHERE is_bridge=1").fetchone()[0]
    database.close()
    print(f"bridge_edges={count}")


if __name__ == "__main__":
    main()
