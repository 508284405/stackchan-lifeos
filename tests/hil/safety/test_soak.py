import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[3] / "tools"))
import soak  # noqa: E402


class DryRunSafetyTests(unittest.TestCase):
    def test_scenarios_and_eight_hour_cadence(self):
        result = soak.run_dry(8 * 60 * 60)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["metrics"]["samples"], 8 * 60 * 60 * 20)
        self.assertGreaterEqual(result["metrics"]["safety_stops"], 1)
        self.assertGreaterEqual(result["metrics"]["feedback_frozen"], 1)
        self.assertGreaterEqual(result["metrics"]["rejected"], 3)
        self.assertEqual(result["control_hz"], 20)


if __name__ == "__main__":
    unittest.main()
