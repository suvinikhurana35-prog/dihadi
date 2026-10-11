"""Fixed sample weather for tests and offline demos. Not real data.

History: every year has heatwaves of three days in May, more of them as
the world warms; every other one reaches the higher tier. Recent days hold
one ordinary heatwave and one severe one, and tomorrow is forecast hot.
"""
from datetime import date, timedelta

import climate

PLACE = {"name": "Ahmedabad", "admin1": "Gujarat", "country": "India", "lat": 23.0258, "lon": 72.5873}


def history(lat, lon, var):
    rows, d = [], date(1980, 1, 1)
    hot = {}
    for y in range(1980, 2025):
        k = max(1, int(climate.warming(y) * 2.5))
        for i in range(k):
            for j in range(3):
                hot[date(y, 5, 2 + i * 5 + j)] = 44.6 if i % 2 else 44.0
    while d.year <= 2024:
        if var == "temperature_2m_max":
            rows.append((d.isoformat(), hot.get(d, 38.0)))
        else:
            rows.append((d.isoformat(), 120.0 if d in hot and d.day % 5 == 2 else 2.0))
        d += timedelta(days=1)
    return rows


def recent(lat, lon, var, past_days=14, forecast_days=3, today=None):
    today = today or date.today()
    hot = {9: 44.0, 8: 44.0, 4: 44.6, 3: 44.7}
    past = [((today - timedelta(days=i)).isoformat(), hot.get(i, 39.0)) for i in range(past_days, 0, -1)]
    ahead = [((today + timedelta(days=i)).isoformat(), 45.0 if i == 1 else 40.0) for i in range(forecast_days)]
    return past, ahead


def install(weather):
    weather.fetch_history = history
    weather.fetch_recent = recent
    weather.find_place = lambda name: dict(PLACE, name=name.strip().title() or PLACE["name"])
