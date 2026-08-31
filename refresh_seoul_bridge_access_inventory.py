#!/usr/bin/env python3
"""Extract Seoul's official Han River bridge-access facilities from its table."""

import argparse
import datetime as dt
import html
import json
import re
from pathlib import Path


SOURCE_URL = "https://hangang.seoul.go.kr/www/contents/841.do?mid=623"
ROW = re.compile(
    r"<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>"
    r"\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*</tr>",
    re.IGNORECASE | re.DOTALL,
)
TAG = re.compile(r"<[^>]+>")


def clean(value: str) -> str:
    return " ".join(html.unescape(TAG.sub(" ", value)).split())


def is_bridge_access(name: str) -> bool:
    bridge = any(token in name for token in ("대교", "철교", "구름다리"))
    access = any(token in name for token in ("승강기", "엘리베이터", "계단"))
    return bridge and access


def canonical_id(park: str, name: str) -> str:
    value = re.sub(r"[^0-9A-Za-z가-힣]+", "-", f"{park}-{name}").strip("-")
    return f"seoul-hangang-{value}"


def extract(document: str, checked_at: str) -> dict:
    facilities = []
    seen = set()
    for park_raw, name_raw, count_raw, manager_raw in ROW.findall(document):
        park, name = clean(park_raw), clean(name_raw)
        if not is_bridge_access(name):
            continue
        key = (park, name)
        if key in seen:
            continue
        seen.add(key)
        facilities.append({
            "id": canonical_id(park, name),
            "park": park,
            "name": name,
            "kind": "elevator" if any(t in name for t in ("승강기", "엘리베이터")) else "steps",
            "cameraCount": int(clean(count_raw)),
            "manager": clean(manager_raw),
            "status": "official_unmatched",
        })
    facilities.sort(key=lambda item: (item["park"], item["name"]))
    if not facilities:
        raise ValueError("No official bridge-access facilities found")
    return {
        "schemaVersion": 1,
        "source": SOURCE_URL,
        "checkedAt": checked_at,
        "facilities": facilities,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("html", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--checked-at", default=dt.date.today().isoformat())
    args = parser.parse_args()
    inventory = extract(args.html.read_text(encoding="utf-8"), args.checked_at)
    args.output.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"official_bridge_access={len(inventory['facilities'])}")


if __name__ == "__main__":
    main()
