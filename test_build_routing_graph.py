import unittest

from build_routing_graph import bicycle_profile


class BicycleProfileTests(unittest.TestCase):
    def test_rejects_motorways_but_keeps_safe_footway_connectors(self):
        self.assertIsNone(bicycle_profile({"highway": "motorway"}, False))
        connector = bicycle_profile({"highway": "footway"}, False)
        self.assertIsNotNone(connector)
        self.assertGreaterEqual(connector[0], 3.8)
        self.assertIsNone(bicycle_profile(
            {"highway": "footway", "surface": "ground"}, False
        ))

    def test_explicit_bicycle_access_overrides_general_access(self):
        profile = bicycle_profile(
            {"highway": "path", "access": "private", "bicycle": "designated"}, False
        )
        self.assertIsNotNone(profile)
        self.assertEqual(profile[1], 1)

    def test_public_foot_access_service_is_a_high_cost_dismount_connector(self):
        profile = bicycle_profile(
            {"highway": "service", "access": "no", "foot": "yes"}, False
        )
        self.assertIsNotNone(profile)
        self.assertGreaterEqual(profile[0], 4.2)
        self.assertIsNone(bicycle_profile(
            {"highway": "service", "access": "no"}, False
        ))

    def test_bicycle_prohibited_bridge_is_walk_bike_fallback_only(self):
        profile = bicycle_profile(
            {"highway": "primary", "bridge": "yes", "bicycle": "no"}, False
        )
        self.assertIsNotNone(profile)
        self.assertGreaterEqual(profile[0], 8)

    def test_rejects_unmapped_hiking_and_bad_surface_paths(self):
        self.assertIsNone(bicycle_profile(
            {"highway": "path", "surface": "ground", "foot": "designated"}, False
        ))
        self.assertIsNone(bicycle_profile(
            {"highway": "path", "surface": "asphalt", "smoothness": "impassable"}, False
        ))

    def test_protected_cycle_track_beats_lane_and_plain_road(self):
        protected = bicycle_profile(
            {"highway": "residential", "cycleway:right": "track"}, False
        )
        lane = bicycle_profile(
            {"highway": "residential", "cycleway:right": "lane"}, False
        )
        plain = bicycle_profile({"highway": "residential"}, False)
        self.assertLess(protected[0], lane[0])
        self.assertLess(lane[0], plain[0])
        self.assertEqual(protected[1], 1)

    def test_dismount_connector_is_kept_but_penalized(self):
        profile = bicycle_profile(
            {"highway": "path", "surface": "asphalt", "bicycle": "dismount"}, False
        )
        self.assertGreaterEqual(profile[0], 2.4)

    def test_public_pedestrian_and_unknown_surface_paths_are_walkable(self):
        pedestrian = bicycle_profile({"highway": "pedestrian"}, False)
        unknown_path = bicycle_profile({"highway": "path"}, False)
        prohibited_riding = bicycle_profile(
            {"highway": "footway", "bicycle": "no", "foot": "yes"}, False
        )
        self.assertGreaterEqual(pedestrian[0], 3.8)
        self.assertGreaterEqual(unknown_path[0], 4.2)
        self.assertGreaterEqual(prohibited_riding[0], 3.8)


if __name__ == "__main__":
    unittest.main()
