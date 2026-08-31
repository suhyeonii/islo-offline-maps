import sqlite3
import unittest

from apply_official_bridge_access import apply, candidate_score
from refresh_seoul_bridge_access_inventory import extract


class OfficialInventoryTests(unittest.TestCase):
    def test_extracts_only_bridge_access_and_deduplicates_rows(self):
        document = """
        <tr><td>망원한강공원</td><td>양화대교북단 승강기</td><td>3</td><td>센터</td></tr>
        <tr><td>망원한강공원</td><td>양화대교북단 승강기</td><td>3</td><td>센터</td></tr>
        <tr><td>망원한강공원</td><td>망원나들목 승강기</td><td>2</td><td>센터</td></tr>
        """
        inventory = extract(document, "2026-08-29")
        self.assertEqual(len(inventory["facilities"]), 1)
        self.assertEqual(inventory["facilities"][0]["kind"], "elevator")

    def test_requires_matching_bridge_side_and_qualifier(self):
        official = {"name": "양화대교 남단 하류 승강기", "kind": "elevator"}
        self.assertGreater(candidate_score(official, "양화대교 남단 하류 엘리베이터"), 0)
        self.assertEqual(candidate_score(official, "양화대교 북단 하류 엘리베이터"), -1)
        self.assertEqual(candidate_score(official, "양화대교 남단 상류 엘리베이터"), -1)

    def test_unresolved_official_facility_is_not_fabricated(self):
        db = sqlite3.connect(":memory:")
        db.executescript("""
          CREATE TABLE nodes(id INTEGER PRIMARY KEY,lat REAL,lon REAL,elevation REAL);
          CREATE TABLE edges(src INTEGER,dst INTEGER,interruption_kind INTEGER,interruption_name TEXT);
          CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
          INSERT INTO metadata VALUES('schemaVersion','10');
        """)
        inventory = {
            "source": "https://example.invalid", "checkedAt": "2026-08-29",
            "facilities": [{"id": "official-1", "park": "강서한강공원",
                            "name": "가양대교남단 승강기", "kind": "elevator"}],
        }
        matched, ambiguous, unresolved = apply(db, inventory)
        self.assertEqual((matched, ambiguous, unresolved), (0, 0, 1))
        self.assertEqual(db.execute("SELECT count(*) FROM nodes").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
