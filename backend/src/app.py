"""Dihadi HTTP API (AWS Lambda behind an API Gateway HTTP API).

  POST /simulate            price cover today and in a warmer climate
  POST /calibrate           fit spells to 30 years of a city's weather
  POST /groups              start a fund
  GET  /groups/{id}         the fund: balance, payout days, 3-day forecast
  POST /groups/{id}/check   record trigger days from the last 14 days now
  GET  /groups/{id}/member  the member's screen: today, tomorrow, money (?lang=hi|en)
  GET  /groups/{id}/speech  the same, read aloud by Amazon Polly as MP3 (?lang=hi|en)
"""
import base64
import json
import os

import boto3

import checks
import climate
import messages
import store
import weather
from engine import clean, simulate


class NotFound(Exception):
    pass


def _response(status, payload):
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "access-control-allow-origin": "*"},
        "body": json.dumps(payload),
    }


def _body(event):
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("body must be a JSON object")
    return data


def _num(b, key, lo, hi):
    if key not in b:
        raise ValueError(f"missing field: {key}")
    v = float(b[key])
    if not (lo <= v <= hi):
        raise ValueError(f"{key} must be between {lo} and {hi}")
    return v


def _terms(b):
    """Peril and trigger terms, defaulting to the standard ones for the peril."""
    peril = b.get("peril")
    if peril not in weather.PERILS:
        raise ValueError("peril must be heat or flood")
    d = weather.PERILS[peril]
    lo, hi = d["range"]
    full = {"t1": d["t1"], "t2": d["t2"], "days": d["days"], **b}
    t1 = _num(full, "t1", lo, hi)
    t2 = _num(full, "t2", lo, hi)
    days = int(_num(full, "days", 1, 7))
    if t2 < t1:
        raise ValueError("the higher trigger can't be below the first trigger")
    return peril, t1, t2, days


def _place(b):
    if "lat" in b and "lon" in b:
        return {"name": str(b.get("name") or "Selected place")[:80], "admin1": str(b.get("admin1") or "")[:80],
                "country": "", "lat": _num(b, "lat", -90, 90), "lon": _num(b, "lon", -180, 180)}
    city = str(b.get("city") or "").strip()
    if not city:
        raise ValueError("enter a city")
    return weather.find_place(city[:80])


def _history(place, var):
    """45 years of daily weather for a place, cached in DynamoDB for 30 days."""
    key = f"SERIES#{place['lat']:.2f}#{place['lon']:.2f}#{var}"
    cached = store.cache_get(key)
    if cached:
        return [tuple(r) for r in cached], True
    series = weather.fetch_history(place["lat"], place["lon"], var)
    if not series:
        raise weather.WeatherError("no weather data returned for this place")
    store.cache_put(key, series)
    return series, False


def calibrate_route(b):
    peril, t1, t2, days = _terms(b)
    place = _place(b)
    p = weather.PERILS[peril]
    series, cached = _history(place, p["var"])
    years = weather.yearly_events(series, t1, t2, days)
    years = [y for y in years if y["year"] in climate.HADCRUT5_1961_90]
    ys = [y["year"] for y in years]
    n1 = climate.fit_counts(ys, [len(y["seq"]) for y in years], peril)
    n2 = climate.fit_counts(ys, [y["seq"].count("2") for y in years], peril)
    for y in years:
        y["warming"] = round(climate.warming(y["year"]), 3)
    return {
        "place": place, "peril": peril, "t1": t1, "t2": t2, "days": days,
        "unit": p["unit"], "label": p["label"], "source": weather.SOURCE,
        "climateSource": "HadCRUT5 global mean temperature (Met Office Hadley Centre / UEA CRU), re-based to 1850–1900",
        "todayWarming": climate.TODAY_WARMING,
        "years": years,
        "lam1": n1["rateToday"], "lam2": min(n2["rateToday"], n1["rateToday"]),
        "b1": n1["b"], "se1": n1["se"], "b2": n2["b"], "se2": n2["se"],
        "fit1": n1, "fit2": n2,
        "suggest": weather.trigger_table(series, p["candidates"], days),
        "cached": cached,
    }


def create_group_route(b):
    peril, t1, t2, days = _terms(b)
    name = str(b.get("name") or "").strip()
    if not 1 <= len(name) <= 80:
        raise ValueError("give the fund a name (up to 80 characters)")
    place = b.get("place") or {}
    premium = _num(b, "premiumPerMember", 0, 10000000)
    share = _num(b, "memberShare", 0, 1)
    fields = {
        "name": name,
        "place": {"name": str(place.get("name", ""))[:80], "admin1": str(place.get("admin1", ""))[:80],
                  "lat": _num(place, "lat", -90, 90), "lon": _num(place, "lon", -180, 180)},
        "peril": peril, "t1": t1, "t2": t2, "days": days,
        "payout1": _num(b, "payout1", 1, 100000),
        "payout2": _num(b, "payout2", 1, 100000),
        "cap": int(_num(b, "cap", 0, 20)),
        "members": int(_num(b, "members", 1, 1000000)),
        "premiumPerMember": premium,
        "memberPays": premium * share,
        "sponsorPays": premium * (1 - share),
        "worstYearPayout": _num(b, "worstYearPayout", 0, 1e13),
        "pricedOn": str(b.get("pricedOn", ""))[:80],
    }
    return {"id": store.create_group(fields)}


def _load(gid):
    group = store.get_group(gid) if gid else None
    if not group:
        raise NotFound("no fund with that id")
    return group


def group_view(group, forecast=None, forecast_error=None, new=None):
    events = store.list_events(group["id"])
    collected = group["members"] * group["premiumPerMember"]
    paid = sum(e["payout"] for e in events) * group["members"]
    return {
        "group": group,
        "events": events,
        "totals": {"collected": collected,
                   "fromMembers": group["members"] * group["memberPays"],
                   "fromSponsor": group["members"] * group["sponsorPays"],
                   "paidOut": paid, "balance": collected - paid, "events": len(events)},
        "forecast": forecast,
        "forecastError": forecast_error,
        "changed": new or [],
        "unit": weather.PERILS[group["peril"]]["unit"],
        "label": weather.PERILS[group["peril"]]["label"],
    }


def _forecast(group):
    var = weather.PERILS[group["peril"]]["var"]
    try:
        _, upcoming = weather.fetch_recent(group["place"]["lat"], group["place"]["lon"], var,
                                           past_days=0, forecast_days=3)
        return checks.forecast_view(group, upcoming), None
    except weather.WeatherError as err:
        return None, str(err)


def get_group_route(gid):
    group = _load(gid)
    forecast, err = _forecast(group)
    return group_view(group, forecast, err)


def member_route(gid, lang):
    group = _load(gid)
    return messages.member_view(group, store.list_events(gid), _forecast(group)[0], lang)


# Kajal (neural) speaks both Hindi and Indian English; Aditi (standard) is the fallback.
VOICES = [("Kajal", "neural"), ("Aditi", "standard")]


class SpeechUnavailable(Exception):
    pass


def speak(text, lang):
    if os.environ.get("POLLY_OFF"):
        raise SpeechUnavailable("voice service is off on this server")
    polly = boto3.client("polly")
    code = "hi-IN" if lang == "hi" else "en-IN"
    last = None
    for voice, engine in VOICES:
        try:
            out = polly.synthesize_speech(Text=text, OutputFormat="mp3", VoiceId=voice,
                                          Engine=engine, LanguageCode=code)
            return out["AudioStream"].read()
        except Exception as err:  # voice or engine not offered in this region: try the next
            last = err
    raise SpeechUnavailable(f"voice service failed: {last}")


def speech_route(gid, lang):
    view = member_route(gid, lang)
    audio = speak(view["speech"], view["lang"])
    return {
        "statusCode": 200,
        "headers": {"content-type": "audio/mpeg", "cache-control": "max-age=600",
                    "access-control-allow-origin": "*"},
        "body": base64.b64encode(audio).decode(),
        "isBase64Encoded": True,
    }


def check_route(gid):
    group = _load(gid)
    new, forecast = checks.run_check(group, past_days=14)
    return group_view(group, forecast, new=new)


def handler(event, context):
    route = event.get("routeKey", "")
    gid = (event.get("pathParameters") or {}).get("id")
    lang = (event.get("queryStringParameters") or {}).get("lang", "hi")
    try:
        if route == "POST /simulate":
            return _response(200, {**simulate(clean(_body(event))), "engine": "aws-lambda"})
        if route == "POST /calibrate":
            return _response(200, calibrate_route(_body(event)))
        if route == "POST /groups":
            return _response(201, create_group_route(_body(event)))
        if route == "GET /groups/{id}":
            return _response(200, get_group_route(gid))
        if route == "POST /groups/{id}/check":
            return _response(200, check_route(gid))
        if route == "GET /groups/{id}/member":
            return _response(200, member_route(gid, lang))
        if route == "GET /groups/{id}/speech":
            return speech_route(gid, lang)
        return _response(404, {"error": f"no route {route}"})
    except NotFound as err:
        return _response(404, {"error": str(err)})
    except weather.WeatherError as err:
        return _response(502, {"error": str(err)})
    except SpeechUnavailable as err:
        return _response(503, {"error": str(err)})
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as err:
        return _response(400, {"error": str(err)})
