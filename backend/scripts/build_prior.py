"""Builds the climate-sensitivity prior from real weather in 12 Indian cities.

For each city and peril, sets a city-specific trigger that was crossed
about twice a year on average (so cities are compared like for like),
counts events each year 1980-2024, and fits the Poisson GLM against global
warming. The city slopes are then pooled with a random-effects model
(DerSimonian-Laird): the pooled mean is the prior's centre, and the spread
between cities is the prior's tau. Writes backend/src/prior.json.

  python backend/scripts/build_prior.py            (needs internet, ~2 minutes)
  python backend/scripts/build_prior.py --offline  (sample data, to test the script)
"""
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, os.path.join(HERE, ".."))

import climate  # noqa: E402
import weather  # noqa: E402

CITIES = [
    ("Ahmedabad", 23.03, 72.58), ("Delhi", 28.61, 77.21), ("Jaipur", 26.91, 75.79),
    ("Nagpur", 21.15, 79.09), ("Lucknow", 26.85, 80.95), ("Patna", 25.59, 85.14),
    ("Hyderabad", 17.39, 78.49), ("Chennai", 13.08, 80.27), ("Kolkata", 22.57, 88.36),
    ("Mumbai", 19.08, 72.88), ("Bhopal", 23.26, 77.41), ("Bhubaneswar", 20.30, 85.82),
]
TARGET_PER_YEAR = 2.0
MIN_TAU = 0.10


def threshold_for(series, peril):
    """Lowest candidate trigger crossed no more than ~2 times a year."""
    d = weather.PERILS[peril]
    grid = [x / 2 for x in range(60, 100)] if peril == "heat" else list(range(20, 300, 5))
    years = len({r[0][:4] for r in series})
    for t in grid:
        n = sum(len(y["seq"]) for y in weather.yearly_events(series, t, 1e9, d["days"]))
        if n / years <= TARGET_PER_YEAR:
            return t
    return grid[-1]


def pool(slopes):
    w = [1 / s["se"] ** 2 for s in slopes]
    fixed = sum(wi * s["b"] for wi, s in zip(w, slopes)) / sum(w)
    q = sum(wi * (s["b"] - fixed) ** 2 for wi, s in zip(w, slopes))
    c = sum(w) - sum(wi * wi for wi in w) / sum(w)
    tau2 = max(0.0, (q - (len(slopes) - 1)) / c) if c > 0 else 0.0
    w_re = [1 / (s["se"] ** 2 + tau2) for s in slopes]
    mean = sum(wi * s["b"] for wi, s in zip(w_re, slopes)) / sum(w_re)
    return mean, max(MIN_TAU, math.sqrt(tau2))


def main():
    if "--offline" in sys.argv:
        import fake_weather
        fake_weather.install(weather)
    out = {}
    for peril in ("heat", "flood"):
        var = weather.PERILS[peril]["var"]
        slopes = []
        for name, lat, lon in CITIES:
            try:
                series = weather.fetch_history(lat, lon, var)
            except weather.WeatherError as err:
                print(f"  {name}: skipped ({err})")
                continue
            t = threshold_for(series, peril)
            years = [y for y in weather.yearly_events(series, t, 1e9, weather.PERILS[peril]["days"])
                     if y["year"] in climate.HADCRUT5_1961_90]
            fit = climate.poisson_glm([climate.warming(y["year"]) for y in years], [len(y["seq"]) for y in years])
            if fit:
                slopes.append({"city": name, "trigger": t, "b": fit["b"], "se": fit["seB"]})
                print(f"  {peril:5} {name:12} trigger {t:6} b = {fit['b']:+.2f} ± {fit['seB']:.2f}")
            time.sleep(0 if "--offline" in sys.argv else 1)
        if len(slopes) < 3:
            print(f"{peril}: too few cities, keeping the IPCC prior")
            continue
        b, tau = pool(slopes)
        out[peril] = {"b": round(b, 4), "tau": round(tau, 4),
                      "source": f"Pooled across {len(slopes)} Indian cities (random effects), ERA5 1980–2024",
                      "cities": slopes}
        print(f"{peril}: pooled b = {b:.3f} per °C (x{math.exp(b):.2f} events), tau = {tau:.3f}")
    path = os.path.join(HERE, "..", "src", "prior.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print("Wrote", os.path.normpath(path))


if __name__ == "__main__":
    main()
