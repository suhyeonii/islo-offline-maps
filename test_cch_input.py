import unittest

from build_routing_graph import bicycle_profile


class CCHInputProfileTests(unittest.TestCase):
    def test_cch_input_keeps_local_access_roads(self) -> None:
        for highway in ("residential", "living_street", "unclassified", "service"):
            with self.subTest(highway=highway):
                self.assertIsNotNone(bicycle_profile({"highway": highway}, compact=False))

    def test_legacy_compact_graph_drops_local_access_roads(self) -> None:
        for highway in ("residential", "living_street", "unclassified"):
            with self.subTest(highway=highway):
                self.assertIsNone(bicycle_profile({"highway": highway}, compact=True))

    def test_forbidden_bicycle_access_remains_excluded(self) -> None:
        self.assertIsNone(
            bicycle_profile({"highway": "residential", "bicycle": "no"}, compact=False)
        )


if __name__ == "__main__":
    unittest.main()
