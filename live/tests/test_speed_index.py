import unittest

from live.speed_index import WEIGHTS, compute_speed_index


class SpeedIndexTests(unittest.TestCase):
    def test_weights_sum(self):
        self.assertAlmostEqual(1.0, sum(WEIGHTS.values()), places=5)

    def test_bounds_zero(self):
        r = compute_speed_index(0, 0, 0, 0, 0)
        self.assertEqual(0.0, r["score"])

    def test_bounds_full(self):
        r = compute_speed_index(500, 6, 60, 100, 12)
        self.assertGreaterEqual(r["score"], 95.0)
        self.assertLessEqual(r["score"], 100.0)

    def test_mid_range(self):
        r = compute_speed_index(100, 3, 30, 50, 6)
        self.assertGreater(r["score"], 10.0)
        self.assertLess(r["score"], 90.0)


if __name__ == "__main__":
    unittest.main()
