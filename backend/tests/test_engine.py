import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import climate  # noqa: E402
import engine  # noqa: E402
import weather  # noqa: E402

BASE = dict(lam1=1.8, lam2=0.6, b1=0.6, b2=0.8, se1=0.2, se2=0.25, payout1=750, payout2=1250,
            cap=3, members=500, costOfCapital=0.06, expenseRatio=0.2, memberShare=0.4,
            scenario="ssp585", horizon="mid")


class PricingTests(unittest.TestCase):
    def test_distribution_sums_to_one_and_mean_is_exact(self):
        d = engine.payout_dist(2.0, 0.3, 750, 1250, 0)
        self.assertAlmostEqual(sum(p for _, p in d), 1, places=9)
        self.assertAlmostEqual(sum(a * p for a, p in d), 2.0 * (0.7 * 750 + 0.3 * 1250), places=4)

    def test_cap_limits_payout(self):
        d = engine.payout_dist(5.0, 0.5, 750, 1250, 3)
        self.assertEqual(d[-1][0], 3 * 1250)
        mean = sum(a * p for a, p in d)
        self.assertAlmostEqual(mean, engine.expected_events(5.0, 3) * 1000, places=6)

    def test_premium_build_up(self):
        r = engine.simulate(engine.clean(BASE))
        t = r["today"]
        self.assertAlmostEqual(t["gross"], (t["aal"] + 0.06 * (t["var995"] - t["aal"])) / 0.8)
        self.assertAlmostEqual(t["memberPays"] + t["sponsorPays"], t["gross"])

    def test_warming_raises_price_and_range_brackets_it(self):
        r = engine.simulate(engine.clean(BASE))
        self.assertGreater(r["scenario"]["gross"], r["today"]["gross"])
        self.assertLessEqual(r["scenarioRange"]["low"], r["scenario"]["gross"])
        self.assertGreaterEqual(r["scenarioRange"]["high"], r["scenario"]["gross"])

    def test_no_sensitivity_no_change(self):
        r = engine.simulate(engine.clean({**BASE, "b1": 0, "b2": 0}))
        self.assertAlmostEqual(r["scenario"]["gross"], r["today"]["gross"])

    def test_bad_terms_rejected(self):
        with self.assertRaises(ValueError):
            engine.clean({**BASE, "payout2": 100})


class ClimateTests(unittest.TestCase):
    def test_glm_recovers_known_slope(self):
        rng = random.Random(7)
        xs = [i / 100 for i in range(0, 130)] * 20
        true_a, true_b = 0.2, 0.8

        def pois(lam):
            k, p, u = 0, math.exp(-lam), rng.random()
            f = p
            while u > f:
                k += 1
                p *= lam / k
                f += p
            return k
        ys = [pois(math.exp(true_a + true_b * x)) for x in xs]
        fit = climate.poisson_glm(xs, ys)
        self.assertAlmostEqual(fit["b"], true_b, delta=3 * fit["seB"])
        self.assertLess(fit["seB"], 0.1)

    def test_credibility_weights(self):
        prior = {"b": 0.7, "tau": 0.3}
        noisy = climate.credibility({"b": 2.0, "seB": 3.0}, prior)
        sharp = climate.credibility({"b": 2.0, "seB": 0.01}, prior)
        self.assertLess(noisy["Z"], 0.05)
        self.assertGreater(sharp["Z"], 0.99)
        self.assertAlmostEqual(climate.credibility(None, prior)["b"], 0.7)

    def test_today_warming_matches_hadcrut(self):
        self.assertAlmostEqual(climate.TODAY_WARMING, 1.26, places=2)
        self.assertEqual(engine.TODAY_WARMING, climate.TODAY_WARMING)


class EventTests(unittest.TestCase):
    def test_runs_and_tiers(self):
        vals = [40, 44, 44.5, 44.2, 43, 44, 44, 41, 44]
        rows = [(f"2024-05-{i:02d}", v) for i, v in enumerate(vals, 1)]
        ev = weather.runs(rows, 43.6, 44.1, 2)
        self.assertEqual([(e["start"], e["tier"]) for e in ev], [("2024-05-02", 2), ("2024-05-06", 1)])
        self.assertFalse(ev[0]["open"])

    def test_single_hot_day_does_not_count(self):
        rows = [("2024-05-01", 45), ("2024-05-02", 40)]
        self.assertEqual(weather.runs(rows, 43.6, 44.1, 2), [])


if __name__ == "__main__":
    unittest.main()
