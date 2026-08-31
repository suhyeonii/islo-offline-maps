#!/usr/bin/env python3
"""Conflate versioned Seoul official bridge access with OSM-derived routing nodes.

The official inventory never supplies guessed geometry. It only labels an existing
OSM interruption node when the facility name and topology identify one candidate.
Unresolved official records remain in the database for later OSM/data updates.
"""

import argparse
import json
import re
import shutil
import sqlite3
from pathlib import Path


def normalize(name: str) -> str:
    value = re.sub(r"\s+", "", name)
    value = value.replace("엘리베이터", "승강기")
    value = value.replace("북단하류", "북단하류").replace("남단하류", "남단하류")
    return value


def signature(name: str, kind: str) -> tuple[str | None, str | None, str]:
    normalized = normalize(name)
    bridge_match = re.search(r"([가-힣A-Za-z0-9]+?(?:대교|철교|구름다리))", normalized)
    side_match = re.search(r"(북단|남단)", normalized)
    interruption = "승강기" if kind == "elevator" else "계단"
    return (
        bridge_match.group(1) if bridge_match else None,
        side_match.group(1) if side_match else None,
        interruption,
    )


def candidate_score(official: dict, candidate_name: str) -> int:
    official_bridge, official_side, official_kind = signature(official["name"], official["kind"])
    candidate_bridge, candidate_side, candidate_kind = signature(candidate_name, official["kind"])
    if not official_bridge or official_bridge != candidate_bridge:
        return -1
    if official_kind not in normalize(candidate_name):
        return -1
    score = 10
    if official_side:
        if candidate_side != official_side:
            return -1
        score += 4
    elif candidate_side:
        score += 1
    official_normalized = normalize(official["name"])
    candidate_normalized = normalize(candidate_name)
    if official_normalized == candidate_normalized:
        score += 20
    for qualifier in ("상류", "하류", "제내지", "제외지"):
        if qualifier in official_normalized:
            if qualifier not in candidate_normalized:
                return -1
            score += 3
    return score


def apply(database: sqlite3.Connection, inventory: dict) -> tuple[int, int, int]:
    database.executescript("""
      CREATE TABLE IF NOT EXISTS official_bridge_access(
        id TEXT PRIMARY KEY, park TEXT NOT NULL, name TEXT NOT NULL,
        kind TEXT NOT NULL, source TEXT NOT NULL, checked_at TEXT NOT NULL,
        status TEXT NOT NULL
      ) WITHOUT ROWID;
      CREATE TABLE IF NOT EXISTS official_bridge_access_matches(
        official_id TEXT PRIMARY KEY, node_id INTEGER NOT NULL,
        osm_name TEXT NOT NULL, match_method TEXT NOT NULL,
        FOREIGN KEY(official_id) REFERENCES official_bridge_access(id)
      ) WITHOUT ROWID;
      DELETE FROM official_bridge_access_matches;
      DELETE FROM official_bridge_access;
    """)
    raw_candidates = database.execute("""
      SELECT MIN(n.id),e.interruption_name,e.interruption_kind
      FROM nodes n JOIN edges e ON e.src=n.id
      WHERE e.interruption_kind IN (2,3) AND e.interruption_name IS NOT NULL
      GROUP BY e.interruption_name,e.interruption_kind
    """).fetchall()
    candidates = list(raw_candidates)
    claimed_names = set()
    matched = ambiguous = unresolved = 0
    for official in inventory["facilities"]:
        scored = []
        expected_kind = 3 if official["kind"] == "elevator" else 2
        for node_id, name, kind in candidates:
            if kind != expected_kind:
                continue
            score = candidate_score(official, name)
            if score >= 0:
                scored.append((score, node_id, name))
        scored.sort(reverse=True)
        status = "official_unmatched"
        match = None
        if (scored and scored[0][2] not in claimed_names
                and (len(scored) == 1 or scored[0][0] > scored[1][0])):
            match = scored[0]
            status = "osm_confirmed"
            matched += 1
        elif scored:
            status = "conflict"
            ambiguous += 1
        else:
            unresolved += 1
        database.execute(
            "INSERT INTO official_bridge_access VALUES(?,?,?,?,?,?,?)",
            (official["id"], official["park"], official["name"], official["kind"],
             inventory["source"], inventory["checkedAt"], status),
        )
        if match:
            _, node_id, osm_name = match
            claimed_names.add(osm_name)
            database.execute(
                "INSERT INTO official_bridge_access_matches VALUES(?,?,?,?)",
                (official["id"], node_id, osm_name, "name_topology"),
            )
            database.execute("""
              UPDATE edges SET interruption_name=?
              WHERE interruption_kind=? AND interruption_name=?
            """, (official["name"], expected_kind, osm_name))
    database.execute("UPDATE metadata SET value='11' WHERE key='schemaVersion'")
    database.execute(
        "INSERT OR REPLACE INTO metadata(key,value) VALUES('officialBridgeAccessCheckedAt',?)",
        (inventory["checkedAt"],),
    )
    database.commit()
    return matched, ambiguous, unresolved


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("inventory", type=Path)
    args = parser.parse_args()
    if args.input.resolve() != args.output.resolve():
        shutil.copy2(args.input, args.output)
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    database = sqlite3.connect(args.output)
    try:
        matched, ambiguous, unresolved = apply(database, inventory)
    finally:
        database.close()
    print(f"official_matched={matched} ambiguous={ambiguous} unresolved={unresolved}")


if __name__ == "__main__":
    main()
