"""Which charged battery a down-site alert recommends: the one that has
worked least. Pure ordering, no database.
Run: venv/bin/python -m unittest tests.test_battery_choice -v
"""
import unittest
from datetime import datetime

from db.monitoring_alerts import least_used_first


def bat(id_, number):
    return {"id": id_, "battery_number": number}


class LeastUsedFirst(unittest.TestCase):
    def test_fewest_recent_trips_first(self):
        usage = {1: (3, 10, datetime(2026, 11, 1)), 2: (1, 40, datetime(2026, 11, 10))}
        self.assertEqual([b["id"] for b in least_used_first([bat(1, "BAT-001"), bat(2, "BAT-002")], usage)], [2, 1])

    def test_a_tie_goes_to_the_one_rested_longest_not_the_lowest_number(self):
        usage = {1: (0, 5, datetime(2026, 11, 9)), 2: (0, 5, datetime(2026, 9, 1))}
        self.assertEqual([b["id"] for b in least_used_first([bat(1, "BAT-001"), bat(2, "BAT-002")], usage)], [2, 1])

    def test_never_moved_counts_as_rested_longest(self):
        usage = {1: (0, 2, datetime(2026, 8, 1))}
        self.assertEqual([b["id"] for b in least_used_first([bat(1, "BAT-001"), bat(2, "BAT-009")], usage)], [2, 1])

    def test_then_fewest_trips_ever_then_number(self):
        same = datetime(2026, 10, 1)
        usage = {1: (0, 9, same), 2: (0, 3, same), 3: (0, 3, same)}
        order = least_used_first([bat(1, "BAT-001"), bat(3, "BAT-003"), bat(2, "BAT-002")], usage)
        self.assertEqual([b["id"] for b in order], [2, 3, 1])


if __name__ == "__main__":
    unittest.main()
