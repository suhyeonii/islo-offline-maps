#!/usr/bin/env python3
import argparse
import sqlite3


def nearest(databases, latitude, longitude):
    best = None
    for database in databases:
        row = database.execute(
            "SELECT id, (lat-?)*(lat-?)+(lon-?)*(lon-?) score "
            "FROM nodes WHERE lat BETWEEN ? AND ? ORDER BY score LIMIT 1",
            (latitude, latitude, longitude, longitude, latitude - .1, latitude + .1),
        ).fetchone()
        if row and (best is None or row[1] < best[1]):
            best = row
    return best[0] if best else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("databases", nargs="+")
    parser.add_argument("--from-point", nargs=2, type=float, required=True)
    parser.add_argument("--to-point", nargs=2, type=float, required=True)
    args = parser.parse_args()
    databases = [sqlite3.connect(path) for path in args.databases]
    source = nearest(databases, *args.from_point)
    target = nearest(databases, *args.to_point)
    parent = {}

    def find(value):
        parent.setdefault(value, value)
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    for database in databases:
        for first, second in database.execute("SELECT src,dst FROM edges"):
            a, b = find(first), find(second)
            if a != b:
                parent[b] = a
    connected = source is not None and target is not None and find(source) == find(target)
    print(f"source={source} target={target} connected={str(connected).lower()}")
    raise SystemExit(0 if connected else 1)


if __name__ == "__main__":
    main()
