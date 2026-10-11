"""What a member sees and hears, in Hindi or English.

Kept short and concrete: what tomorrow brings, what to do about it, and
how much has been paid. The same sentences are shown on screen and read
aloud by Amazon Polly, so a member who can't read gets everything.
"""

from datetime import date

LANGS = ("hi", "en")

TEXT = {
    "hi": {
        "today": "आज", "tomorrow": "कल",
        "heat_bad": "{when} बहुत तेज़ गर्मी, {v} डिग्री।",
        "heat_bad_do": "दोपहर 12 से 4 बजे धूप में काम न करें। हर घंटे पानी पिएं, छाँव में आराम करें।",
        "heat_ok": "{when} गर्मी सामान्य, {v} डिग्री।",
        "heat_ok_do": "काम कर सकते हैं। पानी पीते रहें।",
        "rain_bad": "{when} भारी बारिश, {v} मिलीमीटर।",
        "rain_bad_do": "सामान ज़मीन से ऊपर रखें। पानी भरी सड़क पर न चलें।",
        "rain_ok": "{when} भारी बारिश नहीं, {v} मिलीमीटर।",
        "rain_ok_do": "काम कर सकते हैं।",
        "unknown": "{when} का मौसम अभी नहीं मिला।",
        "paid_heat": "इस साल {n} बार लू चली। आपको {amt} रुपये मिले।",
        "paid_flood": "इस साल {n} बार भारी बारिश हुई। आपको {amt} रुपये मिले।",
        "paid_none": "इस साल अभी तक कोई भुगतान नहीं हुआ।",
        "per_heat": "लू पर {a1} रुपये, बहुत तेज़ लू पर {a2} रुपये।",
        "per_flood": "भारी बारिश पर {a1} रुपये, बहुत भारी बारिश पर {a2} रुपये।",
    },
    "en": {
        "today": "Today", "tomorrow": "Tomorrow",
        "heat_bad": "{when}: very hot, {v} degrees.",
        "heat_bad_do": "Don't work in the sun from 12 to 4 in the afternoon. Drink water every hour and rest in the shade.",
        "heat_ok": "{when}: normal heat, {v} degrees.",
        "heat_ok_do": "You can work. Keep drinking water.",
        "rain_bad": "{when}: heavy rain, {v} millimetres.",
        "rain_bad_do": "Keep stock off the floor. Don't walk through flooded roads.",
        "rain_ok": "{when}: no heavy rain, {v} millimetres.",
        "rain_ok_do": "You can work.",
        "unknown": "{when}: no forecast yet.",
        "paid_heat": "Heatwaves this year: {n}. You have been paid {amt} rupees.",
        "paid_flood": "Heavy rain spells this year: {n}. You have been paid {amt} rupees.",
        "paid_none": "No payments yet this year.",
        "per_heat": "{a1} rupees for a heatwave, {a2} rupees for a severe one.",
        "per_flood": "{a1} rupees for heavy rain, {a2} rupees for very heavy rain.",
    },
}


def _rupees(v):
    """Indian digit grouping: 1250000 -> 12,50,000."""
    s = str(int(round(v)))
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


def _day(group, f, label, t):
    if not f or f.get("value") is None:
        return {"state": "unknown", "icon": "cloud", "label": label,
                "say": t["unknown"].format(when=label), "do": ""}
    heat = group["peril"] == "heat"
    v = round(f["value"]) if heat else round(f["value"], 1)
    bad = bool(f.get("trigger"))
    kind = ("heat" if heat else "rain") + ("_bad" if bad else "_ok")
    icon = ("hot" if bad else "sun") if heat else ("rain" if bad else "cloud")
    return {"state": "danger" if bad else "ok", "icon": icon, "label": label, "value": v,
            "say": t[kind].format(when=label, v=v), "do": t[kind + "_do"]}


def member_view(group, events, forecast, lang, year=None):
    if lang not in LANGS:
        lang = "hi"
    t = TEXT[lang]
    forecast = forecast or []
    days = [_day(group, forecast[i] if len(forecast) > i else None, t[k], t)
            for i, k in enumerate(("today", "tomorrow"))]

    if year is None:
        year = str(date.today().year)
    this_year = [e for e in events if e["start"][:4] == year]
    n = len(this_year)
    amount = sum(e["payout"] for e in this_year)
    paid = t["paid_" + group["peril"]].format(n=n, amt=_rupees(amount)) if n else t["paid_none"]
    per_day = t["per_" + group["peril"]].format(a1=_rupees(group["payout1"]), a2=_rupees(group["payout2"]))

    speech = " ".join(part for d in days for part in (d["say"], d["do"]) if part)
    speech += " " + paid + " " + per_day
    return {
        "lang": lang,
        "peril": group["peril"],
        "group": {"name": group["name"], "place": group["place"]["name"]},
        "days": days,
        "money": {"amount": amount, "events": n, "say": paid, "perDaySay": per_day,
                  "tiers": [e["tier"] for e in sorted(this_year, key=lambda e: e["start"])]},
        "speech": speech,
    }
