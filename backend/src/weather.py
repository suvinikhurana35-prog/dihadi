"""Daily weather from Open-Meteo: ERA5 reanalysis for history, and the
forecast API for recent days and the next few days. No API key needed."""
import json
import urllib.parse
import urllib.request

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"
GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"

# What each cover pays on. Heat follows SEWA's parametric heat cover in
# Ahmedabad (two days in a row at or above 43.6 °C; higher tier at 44.1 °C).
# Rain follows the IMD's categories: heavy 64.5 mm, very heavy 115.6 mm in a day.
PERILS = {
    "heat": {"var": "temperature_2m_max", "unit": "°C", "t1": 43.6, "t2": 44.1, "days": 2,
             "payout1": 750, "payout2": 1250, "range": (30.0, 50.0),
             "candidates": [x / 2 for x in range(76, 95)],
             "label": "highest temperature of the day"},
    "flood": {"var": "precipitation_sum", "unit": "mm", "t1": 64.5, "t2": 115.6, "days": 1,
              "payout1": 500, "payout2": 1000, "range": (10.0, 400.0),
              "candidates": [35, 50, 64.5, 80, 100, 115.6, 150, 204.5],
              "label": "rain in a day"},
}
HIST_START, HIST_END = 1980, 2024
SOURCE = f"ERA5 reanalysis (ECMWF) via Open-Meteo, daily, {HIST_START}–{HIST_END}"


class WeatherError(Exception):
    pass


def _get(url, params):
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, headers={"User-Agent": "dihadi/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except Exception as err:  # network, HTTP or JSON errors
        raise WeatherError(f"weather service unavailable: {err}") from err


def find_place(name):
    data = _get(GEOCODE, {"name": name, "count": 10, "language": "en", "format": "json"})
    results = data.get("results") or []
    if not results:
        raise WeatherError(f"no place called '{name}' found")
    best = next((r for r in results if r.get("country_code") == "IN"), results[0])
    return {
        "name": best["name"],
        "admin1": best.get("admin1", ""),
        "country": best.get("country", ""),
        "lat": round(best["latitude"], 4),
        "lon": round(best["longitude"], 4),
    }


def fetch_history(lat, lon, var):
    data = _get(ARCHIVE, {
        "latitude": lat, "longitude": lon,
        "start_date": f"{HIST_START}-01-01", "end_date": f"{HIST_END}-12-31",
        "daily": var, "timezone": "auto",
    })
    d = data.get("daily") or {}
    return list(zip(d.get("time", []), d.get(var, [])))


def fetch_recent(lat, lon, var, past_days=14, forecast_days=3):
    """Returns (past, upcoming). Upcoming starts with today."""
    data = _get(FORECAST, {
        "latitude": lat, "longitude": lon, "daily": var, "timezone": "auto",
        "past_days": past_days, "forecast_days": forecast_days,
    })
    d = data.get("daily") or {}
    rows = list(zip(d.get("time", []), d.get(var, [])))
    return rows[:-forecast_days], rows[-forecast_days:]


def runs(rows, t1, t2, min_days):
    """rows: [(YYYY-MM-DD, value)] in date order. Returns events: runs of
    consecutive days at or above t1 that last at least min_days. An event
    reaches tier 2 if it holds min_days in a row at or above t2.
    A run still going on the last row is marked open."""
    out, cur = [], None

    def close(r, is_open):
        if r and r["len"] >= min_days:
            r["tier"] = 2 if r["best2"] >= min_days else 1
            r["open"] = is_open
            out.append({k: r[k] for k in ("start", "end", "len", "peak", "tier", "open")})

    for date, v in rows:
        if v is not None and v >= t1:
            if cur is None:
                cur = {"start": date, "end": date, "len": 0, "peak": v, "run2": 0, "best2": 0}
            cur["end"] = date
            cur["len"] += 1
            cur["peak"] = max(cur["peak"], v)
            cur["run2"] = cur["run2"] + 1 if v >= t2 else 0
            cur["best2"] = max(cur["best2"], cur["run2"])
        else:
            close(cur, False)
            cur = None
    close(cur, True)
    return out


def yearly_events(series, t1, t2, min_days):
    """Events per calendar year, as a string of tiers in date order: '121'."""
    by_year = {}
    for date, v in series:
        by_year.setdefault(int(date[:4]), []).append((date, v))
    return [{"year": y, "seq": "".join(str(e["tier"]) for e in runs(by_year[y], t1, t2, min_days))}
            for y in sorted(by_year)]


def trigger_table(series, candidates, min_days, last_years=10):
    """How often each candidate trigger would have paid, per year, recently."""
    end = max(int(d[:4]) for d, _ in series)
    recent = [(d, v) for d, v in series if int(d[:4]) > end - last_years]
    n = len({d[:4] for d, _ in recent}) or 1
    return [{"t": t, "perYear": sum(len(y["seq"]) for y in yearly_events(recent, t, 1e9, min_days)) / n}
            for t in candidates]
