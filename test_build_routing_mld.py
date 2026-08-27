import unittest

from build_routing_mld import (
    PROFILE_BICYCLE,
    PROFILE_SHORTEST,
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


if __name__ == "__main__":
    unittest.main()
