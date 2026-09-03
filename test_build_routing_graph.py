import os
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from build_routing_graph import ElevationProvider, bicycle_profile


class RoutingSchemaTests(unittest.TestCase):
    def test_regional_graph_omits_legacy_hierarchy_tables(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "regional.sqlite"
            subprocess.run(
                [sys.executable, str(Path(__file__).with_name("build_routing_graph.py")), str(output)],
                input="",
                text=True,
                check=True,
            )
            with sqlite3.connect(output) as database:
                objects = {
                    row[0]
                    for row in database.execute(
                        "SELECT name FROM sqlite_master WHERE type IN ('table','index')"
                    )
                }
                metadata = dict(database.execute("SELECT key,value FROM metadata"))

            self.assertFalse(any(name.startswith("hierarchy_") for name in objects))
            self.assertEqual(metadata["routingHierarchy"], "none")
            self.assertIn("nodes_lat_lon", objects)
            self.assertIn("edges_destination", objects)


class ElevationProviderTests(unittest.TestCase):
    def test_tile_name_formatting(self):
        self.assertEqual(ElevationProvider.tile_name(37.5665, 126.9780), "N37E126")
        self.assertEqual(ElevationProvider.tile_name(-33.8688, 151.2093), "S34E151")
        self.assertEqual(ElevationProvider.tile_name(40.7128, -74.0060), "N40W075")

    def test_returns_zero_when_no_dem_dir_or_missing_tile(self):
        provider = ElevationProvider(None)
        self.assertEqual(provider.get_elevation(37.5, 126.5), 0)

        with tempfile.TemporaryDirectory() as empty_dir:
            provider = ElevationProvider(empty_dir)
            self.assertEqual(provider.get_elevation(37.5, 126.5), 0)

    def test_reads_and_interpolates_synthetic_hgt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a synthetic 1201x1201 SRTM-3 tile for N37E126
            dim = 1201
            # Fill with a flat plane elevation = 150m
            flat_tile = struct.pack(f">{dim * dim}h", *([150] * (dim * dim)))
            tile_path = os.path.join(temp_dir, "N37E126.hgt")
            with open(tile_path, "wb") as f:
                f.write(flat_tile)

            provider = ElevationProvider(temp_dir)
            ele = provider.get_elevation(37.5, 126.5)
            self.assertEqual(ele, 150)

    def test_reads_hgt_from_zip_archive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dim = 1201
            flat_tile = struct.pack(f">{dim * dim}h", *([220] * (dim * dim)))
            zip_path = os.path.join(temp_dir, "N37E126.hgt.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("N37E126.hgt", flat_tile)

            provider = ElevationProvider(temp_dir)
            ele = provider.get_elevation(37.25, 126.75)
            self.assertEqual(ele, 220)


class BicycleProfileTests(unittest.TestCase):
    def test_rejects_motorways_but_keeps_safe_footway_connectors(self):
        self.assertIsNone(bicycle_profile({"highway": "motorway"}, False))
        self.assertIsNone(bicycle_profile(
            {"highway": "primary", "motorroad": "yes", "bicycle": "yes"}, False
        ))
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

    def test_bicycle_prohibited_bridge_is_excluded(self):
        self.assertIsNone(bicycle_profile(
            {"highway": "primary", "bridge": "yes", "bicycle": "no"}, False
        ))

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
        self.assertEqual(protected[2], 0)

    def test_dedicated_cycleway_is_distinct_and_gets_strongest_preference(self):
        dedicated = bicycle_profile({"highway": "cycleway"}, False)
        protected = bicycle_profile(
            {"highway": "residential", "cycleway:right": "track"}, False
        )
        lane = bicycle_profile(
            {"highway": "residential", "cycleway:right": "lane"}, False
        )
        self.assertEqual(dedicated[1:], (1, 1))
        self.assertEqual(protected[1:], (1, 0))
        self.assertEqual(lane[1:], (1, 0))
        self.assertLess(dedicated[0], protected[0])
        self.assertLess(protected[0], lane[0])

    def test_bicycle_shoulder_is_friendly_but_generic_shoulder_needs_permission(self):
        bicycle_shoulder = bicycle_profile(
            {"highway": "primary", "cycleway": "shoulder"}, False
        )
        permitted_shoulder = bicycle_profile(
            {"highway": "primary", "shoulder": "yes", "bicycle": "yes"}, False
        )
        plain = bicycle_profile({"highway": "primary"}, False)
        self.assertEqual(bicycle_shoulder[1], 1)
        self.assertEqual(bicycle_shoulder[2], 0)
        self.assertLess(bicycle_shoulder[0], permitted_shoulder[0])
        self.assertLess(permitted_shoulder[0], plain[0])

    def test_dismount_connector_is_kept_but_penalized(self):
        profile = bicycle_profile(
            {"highway": "path", "surface": "asphalt", "bicycle": "dismount"}, False
        )
        self.assertGreaterEqual(profile[0], 2.4)

    def test_public_steps_are_retained_only_as_last_resort_interruption(self):
        profile = bicycle_profile({"highway": "steps", "foot": "yes"}, False)
        self.assertIsNotNone(profile)
        self.assertGreaterEqual(profile[0], 500)
        self.assertIsNone(bicycle_profile(
            {"highway": "steps", "access": "private"}, False
        ))

    def test_public_pedestrian_is_walkable_but_unknown_path_is_not(self):
        pedestrian = bicycle_profile({"highway": "pedestrian"}, False)
        unknown_path = bicycle_profile({"highway": "path"}, False)
        paved_path = bicycle_profile({"highway": "path", "surface": "asphalt"}, False)
        prohibited_riding = bicycle_profile(
            {"highway": "footway", "bicycle": "no", "foot": "yes"}, False
        )
        self.assertGreaterEqual(pedestrian[0], 3.8)
        self.assertIsNone(unknown_path)
        self.assertGreaterEqual(paved_path[0], 4.2)
        self.assertIsNone(prohibited_riding)

    def test_rejects_trail_metadata_even_when_surface_is_missing(self):
        self.assertIsNone(bicycle_profile(
            {"highway": "path", "trail_visibility": "good"}, False
        ))
        self.assertIsNone(bicycle_profile(
            {"highway": "path", "mtb:scale": "1"}, False
        ))


if __name__ == "__main__":
    unittest.main()
