"""Dihadi pricing engine (pure Python, no dependencies).

Cover pays each member a fixed amount per event (a heatwave or a heavy-rain
spell): payout1 for an event that crosses the trigger, payout2 if it also
crosses the higher trigger, for at most `cap` events a year.

Events per year are Poisson; each event independently reaches the higher
tier with probability p2. The yearly payout is therefore a compound
distribution on a lattice, and it is computed exactly here rather than
simulated, so there is no sampling noise in the 1-in-200 figure.

The same code is mirrored line for line in frontend/index.html.
"""
import math

# Global warming in °C above 1850-1900. IPCC AR6 WG1 SPM Table SPM.1,
# central estimates for 2021-40, 2041-60 and 2081-2100.
WARMING = {
    "ssp126": {"near": 1.5, "mid": 1.7, "late": 1.8},
    "ssp245": {"near": 1.5, "mid": 2.0, "late": 2.7},
    "ssp585": {"near": 1.6, "mid": 2.4, "late": 4.4},
}
TODAY_WARMING = 1.26  # HadCRUT5 average for 2015-2024, see climate.py
RETURN_PERIODS = [2, 5, 10, 20, 50, 100, 200]


def count_pmf(lam, cap):
    """P(paid events = m) for m = 0..cap, with N ~ Poisson(lam) and M = min(N, cap).
    cap = 0 means no cap."""
    p = math.exp(-lam)
    out = [p]
    total = p
    n = 0
    limit = cap if cap > 0 else 10**6
    while n + 1 < limit:
        n += 1
        p *= lam / n
        out.append(p)
        total += p
        if cap == 0 and 1 - total < 1e-13 and n > lam:
            return out
    if cap > 0:
        out.append(max(0.0, 1 - total))  # P(N >= cap)
    return out


def payout_dist(lam1, p2, payout1, payout2, cap):
    """Exact distribution of one member's yearly payout: list of (amount, prob)."""
    probs = {}
    for m, pm in enumerate(count_pmf(lam1, cap)):
        if pm <= 0:
            continue
        for j in range(m + 1):  # j of the m paid events reach the higher tier
            pj = pm * math.comb(m, j) * p2 ** j * (1 - p2) ** (m - j)
            if pj > 0:
                amt = round((m - j) * payout1 + j * payout2, 6)
                probs[amt] = probs.get(amt, 0.0) + pj
    return sorted(probs.items())


def quantile(dist, q):
    c = 0.0
    for amt, p in dist:
        c += p
        if c >= q - 1e-12:
            return amt
    return dist[-1][0]


def expected_events(lam1, cap):
    return sum(m * p for m, p in enumerate(count_pmf(lam1, cap)))


def summarise(dist, coc_rate, expense, member_share):
    mean = sum(a * p for a, p in dist)
    sd = math.sqrt(max(0.0, sum((a - mean) ** 2 * p for a, p in dist)))
    var995 = quantile(dist, 0.995)
    tail = [(a, p) for a, p in dist if a >= quantile(dist, 0.99)]
    tvar = sum(a * p for a, p in tail) / sum(p for _, p in tail)
    capital = max(var995 - mean, 0.0)
    coc = coc_rate * capital
    gross = (mean + coc) / (1 - expense)
    p_zero = sum(p for a, p in dist if a == 0)
    return {
        "aal": mean, "sd": sd, "var995": var995, "tvar99": tvar, "pAny": 1 - p_zero,
        "aep": [{"rp": rp, "loss": quantile(dist, 1 - 1 / rp)} for rp in RETURN_PERIODS],
        "capital": capital, "coc": coc, "expenses": gross - mean - coc, "gross": gross,
        "memberPays": gross * member_share, "sponsorPays": gross * (1 - member_share),
    }


def book(p, delta_t, b1, b2):
    lam1 = p["lam1"] * math.exp(b1 * delta_t)
    lam2 = min(p["lam2"] * math.exp(b2 * delta_t), lam1)
    p2 = lam2 / lam1 if lam1 > 0 else 0.0
    dist = payout_dist(lam1, p2, p["payout1"], p["payout2"], p["cap"])
    out = summarise(dist, p["costOfCapital"], p["expenseRatio"], p["memberShare"])
    out.update({"events": lam1, "higherTierShare": p2, "paidEvents": expected_events(lam1, p["cap"])})
    out["pool"] = {"premium": out["gross"] * p["members"], "capital": out["capital"] * p["members"],
                   "worstYear": out["var995"] * p["members"]}
    return out


def simulate(p):
    delta_t = WARMING[p["scenario"]][p["horizon"]] - TODAY_WARMING
    z = 1.96
    low = book(p, delta_t, p["b1"] - z * p["se1"], p["b2"] - z * p["se2"])
    high = book(p, delta_t, p["b1"] + z * p["se1"], p["b2"] + z * p["se2"])
    lo, hi = sorted([low["gross"], high["gross"]])
    return {
        "deltaT": delta_t,
        "today": book(p, 0.0, p["b1"], p["b2"]),
        "scenario": book(p, delta_t, p["b1"], p["b2"]),
        "scenarioRange": {"low": lo, "high": hi},
    }


BOUNDS = {
    "lam1": (0, 60), "lam2": (0, 60),
    "b1": (-3, 3), "b2": (-3, 3), "se1": (0, 3), "se2": (0, 3),
    "payout1": (1, 100000), "payout2": (1, 100000),
    "cap": (0, 20), "members": (1, 10000000),
    "costOfCapital": (0, 0.5), "expenseRatio": (0, 0.6), "memberShare": (0, 1),
}


def clean(body):
    p = {}
    for key, (lo, hi) in BOUNDS.items():
        if key not in body:
            raise ValueError(f"missing field: {key}")
        v = float(body[key])
        if not (lo <= v <= hi):
            raise ValueError(f"{key} must be between {lo} and {hi}")
        p[key] = v
    p["cap"] = int(p["cap"])
    p["members"] = int(p["members"])
    if p["payout2"] < p["payout1"]:
        raise ValueError("the higher-tier payout can't be below the first one")
    if body.get("scenario") not in WARMING:
        raise ValueError("scenario must be one of " + ", ".join(WARMING))
    if body.get("horizon") not in ("near", "mid", "late"):
        raise ValueError("horizon must be near, mid or late")
    p["scenario"] = body["scenario"]
    p["horizon"] = body["horizon"]
    return p
