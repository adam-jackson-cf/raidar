import unittest

from src.reporting import build_report


class BuildReportTests(unittest.TestCase):
    def test_groups_and_sorts_team_totals(self):
        rows = [
            {"team": "Core", "points": 4},
            {"team": "Edge", "points": 3},
            {"team": "Core", "points": 2},
        ]
        self.assertEqual(build_report(rows), "Core: 6\nEdge: 3\nTOTAL: 9")

    def test_rejects_missing_team(self):
        with self.assertRaises(ValueError):
            build_report([{"points": 2}])


if __name__ == "__main__":
    unittest.main()
