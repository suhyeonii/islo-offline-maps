import unittest
import sqlite3

from build_routing_mld import (
    PROFILE_BICYCLE,
    PROFILE_SHORTEST,
    build_component_portals,
    overlay_shortcuts_from,
    shortcuts_from,
)


class RoutingMLDTests(unittest.TestCase):
    def test_upper_level_overlay_preserves_directed_costs(self):
        adjacency = {
            1: [(2, 3.0, 10.0), (3, 20.0, 12.0)],
            2: [(3, 4.0, 10.0)],
        }
        result = overlay_shortcuts_from(1, {1, 3}, adjacency)
        self.assertEqual(result, [(3, 7.0, 20.0)])

    def test_profile_specific_shortcuts_preserve_path(self):
        # Direct edge is shorter, while the two cycleway edges have lower
        # bicycle-profile cost. Each metric must retain its own exact node path.
        adjacency = {
            1: [
                (1, 2, 10.0, 4.0, 1, 0),
                (1, 3, 15.0, 15.0, 0, 0),
            ],
            2: [(2, 3, 10.0, 4.0, 1, 0)],
        }
        bicycle = shortcuts_from(1, {1, 3}, adjacency, PROFILE_BICYCLE)
        shortest = shortcuts_from(1, {1, 3}, adjacency, PROFILE_SHORTEST)
        self.assertEqual(bicycle[0][3], [1, 2, 3])
        self.assertEqual(shortest[0][3], [1, 3])

    def test_component_portals_separate_disconnected_overlay_islands(self):
        database = sqlite3.connect(":memory:")
        database.executescript(
            """
            CREATE TABLE hierarchy_level1_portals(node_id INTEGER,level1_cell INTEGER);
            CREATE TABLE hierarchy_shortcuts(level INTEGER,cell_id INTEGER,profile INTEGER,
                src INTEGER,dst INTEGER,cost REAL,meters REAL);
            CREATE TABLE edges(src INTEGER,dst INTEGER);
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
            INSERT INTO hierarchy_level1_portals VALUES
                (1,10),(2,10),(3,11),(4,20),(5,20);
            INSERT INTO hierarchy_shortcuts VALUES
                (1,10,0,1,2,1,1),(1,20,0,4,5,1,1);
            INSERT INTO edges VALUES(2,3);
            """
        )
        build_component_portals(database)
        components = dict(database.execute(
            "SELECT node_id,component_id FROM hierarchy_component_portals"
        ))
        self.assertEqual(components[1], components[3])
        self.assertEqual(components[4], components[5])
        self.assertNotEqual(components[1], components[4])


if __name__ == "__main__":
    unittest.main()
