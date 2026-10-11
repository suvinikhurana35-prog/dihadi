"""Links a city's heatwave or heavy-rain count to global warming.

1. Global temperature: HadCRUT5 (Met Office Hadley Centre / UEA CRU),
   annual global mean, re-based from 1961-1990 to 1850-1900 so it sits on
   the same scale as the IPCC warming levels.
2. A Poisson GLM, log(events per year) = a + b * global warming, fitted by
   iteratively reweighted least squares. b is the city's climate sensitivity:
   each +1 °C of global warming multiplies events by exp(b). Standard errors
   are inflated for over-dispersion (quasi-Poisson).
3. Forty-five years is a short record, so b is credibility-weighted towards
   a prior (Bühlmann style): Z = tau^2 / (tau^2 + se^2). The prior is pooled
   across Indian cities by scripts/build_prior.py, or else taken from IPCC AR6.
"""
import json
import math
import os

# HadCRUT.5.0.2.0 analysis, global annual mean, deg C relative to 1961-1990.
HADCRUT5_1961_90 = {
    1975: -0.110754214, 1976: -0.2158369, 1977: 0.1030885, 1978: 0.0052559716,
    1979: 0.09085814, 1980: 0.19607204, 1981: 0.25001204, 1982: 0.034268282,
    1983: 0.22380984, 1984: 0.04799352, 1985: 0.049729742, 1986: 0.09568698,
    1987: 0.2430264, 1988: 0.2821517, 1989: 0.1792503, 1990: 0.36058238,
    1991: 0.33889654, 1992: 0.12489683, 1993: 0.16570719, 1994: 0.23354979,
    1995: 0.37686613, 1996: 0.27668938, 1997: 0.4223085, 1998: 0.5773417,
    1999: 0.32448497, 2000: 0.33108476, 2001: 0.48928034, 2002: 0.5434665,
    2003: 0.54417014, 2004: 0.46737072, 2005: 0.6068625, 2006: 0.5725527,
    2007: 0.5917013, 2008: 0.46564984, 2009: 0.5967816, 2010: 0.6803714,
    2011: 0.53769773, 2012: 0.57760704, 2013: 0.6235753, 2014: 0.67287165,
    2015: 0.8251144, 2016: 0.9329271, 2017: 0.84517425, 2018: 0.76265407,
    2019: 0.8910726, 2020: 0.9229205, 2021: 0.76190555, 2022: 0.8013053,
    2023: 1.1003127, 2024: 1.1808001, 2025: 1.1902827,
}
# Mean HadCRUT5 anomaly over 1850-1900 is -0.3565 °C (same file, years 1850-1900).
PREINDUSTRIAL_OFFSET = 0.3565


def warming(year):
    """Global warming in °C above 1850-1900 for a calendar year."""
    return HADCRUT5_1961_90[year] + PREINDUSTRIAL_OFFSET


TODAY_YEARS = range(2015, 2025)
TODAY_WARMING = round(sum(warming(y) for y in TODAY_YEARS) / len(TODAY_YEARS), 2)  # 1.26

# Fallback prior when no pooled estimate has been built: IPCC AR6 WG1 SPM
# (Figure SPM.6). Over land, a 1-in-10-year hot extreme is 2.8x as frequent
# at 1 °C and 5.6x at 2 °C, so b = ln(5.6 / 2.8) = 0.69 per °C. Heavy
# 1-day rain: 1.3x at 1 °C, 1.7x at 2 °C, so b = ln(1.7 / 1.3) = 0.27.
DEFAULT_PRIOR = {
    "heat": {"b": 0.69, "tau": 0.35, "source": "IPCC AR6 WG1, Figure SPM.6 (hot extremes over land)"},
    "flood": {"b": 0.27, "tau": 0.20, "source": "IPCC AR6 WG1, Figure SPM.6 (heavy 1-day rain over land)"},
}


def load_prior():
    path = os.path.join(os.path.dirname(__file__), "prior.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {k: {**DEFAULT_PRIOR[k], **data[k]} for k in DEFAULT_PRIOR if k in data} or DEFAULT_PRIOR
    except (OSError, ValueError, KeyError):
        return DEFAULT_PRIOR


def poisson_glm(xs, ys, iters=50):
    """Fit log(mu) = a + b*(x - xbar). Returns a, b, se_b, xbar, dispersion,
    or None when there are too few events to say anything."""
    n = len(xs)
    if n < 5 or sum(ys) < 3:
        return None
    xbar = sum(xs) / n
    c = [x - xbar for x in xs]
    a, b = math.log(sum(ys) / n), 0.0
    for _ in range(iters):
        mu = [math.exp(a + b * ci) for ci in c]
        z = [a + b * ci + (y - m) / m for ci, y, m in zip(c, ys, mu)]
        s0 = sum(mu)
        s1 = sum(m * ci for m, ci in zip(mu, c))
        s2 = sum(m * ci * ci for m, ci in zip(mu, c))
        t0 = sum(m * zi for m, zi in zip(mu, z))
        t1 = sum(m * ci * zi for m, ci, zi in zip(mu, c, z))
        det = s0 * s2 - s1 * s1
        if det <= 0:
            return None
        na, nb = (s2 * t0 - s1 * t1) / det, (s0 * t1 - s1 * t0) / det
        done = abs(na - a) < 1e-10 and abs(nb - b) < 1e-10
        a, b = na, nb
        if done:
            break
    mu = [math.exp(a + b * ci) for ci in c]
    s0 = sum(mu)
    s1 = sum(m * ci for m, ci in zip(mu, c))
    s2 = sum(m * ci * ci for m, ci in zip(mu, c))
    pearson = sum((y - m) ** 2 / m for y, m in zip(ys, mu))
    phi = max(1.0, pearson / (n - 2))
    se_b = math.sqrt(phi * s0 / (s0 * s2 - s1 * s1))
    return {"a": a, "b": b, "seB": se_b, "xbar": xbar, "dispersion": phi}


def credibility(fit, prior):
    """Blend the city's fitted slope with the prior. With no usable fit the
    prior is used on its own (Z = 0)."""
    tau2 = prior["tau"] ** 2
    if fit is None:
        return {"b": prior["b"], "se": prior["tau"], "Z": 0.0}
    se2 = fit["seB"] ** 2
    z = tau2 / (tau2 + se2)
    return {"b": z * fit["b"] + (1 - z) * prior["b"],
            "se": math.sqrt(1 / (1 / tau2 + 1 / se2)), "Z": z}


def rate_at(xs, ys, b, x0):
    """Maximum-likelihood event rate at warming x0 when the slope is fixed at b:
    the Poisson intercept then has a closed form."""
    denom = sum(math.exp(b * (x - x0)) for x in xs)
    return sum(ys) / denom if denom > 0 else 0.0


def fit_counts(years, counts, peril):
    """years: calendar years; counts: events in each. Returns the fitted
    and credibility-weighted sensitivity and today's rate."""
    prior = load_prior()[peril]
    xs = [warming(y) for y in years]
    fit = poisson_glm(xs, counts)
    cred = credibility(fit, prior)
    return {
        "fit": fit,
        "prior": {"b": prior["b"], "tau": prior["tau"], "source": prior["source"]},
        "b": cred["b"], "se": cred["se"], "Z": cred["Z"],
        "rateToday": rate_at(xs, counts, cred["b"], TODAY_WARMING),
    }
