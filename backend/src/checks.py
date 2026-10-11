"""Trigger checks shared by the API (check now) and the daily monitor.

An event is a run of consecutive days at or above the trigger lasting at
least the fund's `days`. It is paid as soon as it qualifies, upgraded to the
higher tier if the heat or rain gets worse, and paid once however long it
lasts. A run that continues across two checks is matched to the event
already recorded, so nothing is paid twice. At most `cap` events a year pay.
"""
import os
from datetime import date, timedelta

import boto3

import store
import weather


def tier_amount(group, tier):
    return group["payout2"] if tier == 2 else group["payout1"]


def forecast_view(group, upcoming):
    return [{"date": d, "value": v, "trigger": v is not None and v >= group["t1"]} for d, v in upcoming]


def _prev_day(iso):
    return (date.fromisoformat(iso) - timedelta(days=1)).isoformat()


def apply_events(group, past_rows):
    """Record new events and tier upgrades from past readings.
    Returns what changed: [{..event, 'change': 'new' | 'upgrade'}]."""
    known = store.list_events(group["id"])
    changed = []
    for run in weather.runs(past_rows, group["t1"], group["t2"], group["days"]):
        same = next((e for e in known
                     if e["start"] <= run["end"] and e["end"] >= _prev_day(run["start"])), None)
        if same:
            fields = {}
            if run["end"] > same["end"]:
                fields["end"] = run["end"]
            if run["peak"] > same["peak"]:
                fields["peak"] = run["peak"]
            if run["tier"] > same["tier"]:
                fields["tier"] = run["tier"]
                fields["payout"] = tier_amount(group, run["tier"])
            if fields:
                store.update_event(group["id"], same["start"], fields)
                same.update(fields)
                if "tier" in fields:
                    changed.append({**same, "change": "upgrade"})
            continue
        year = run["start"][:4]
        if group["cap"] and sum(1 for e in known if e["start"][:4] == year) >= group["cap"]:
            continue
        ev = {"start": run["start"], "end": run["end"], "peak": run["peak"], "tier": run["tier"],
              "payout": tier_amount(group, run["tier"])}
        if store.put_event(group["id"], ev):
            known.append(ev)
            changed.append({**ev, "change": "new"})
    return changed


def notify(group, changed, forecast):
    """Email the fund operator through SNS. Silent if no topic is set."""
    topic = os.environ.get("TOPIC_ARN")
    ahead = [f for f in forecast[1:] if f["trigger"]]
    if not topic or not (changed or ahead):
        return
    unit = weather.PERILS[group["peril"]]["unit"]
    lines = [f"Dihadi fund: {group['name']} ({group['place']['name']})", ""]
    for e in changed:
        what = "Payout due" if e["change"] == "new" else "Upgraded to the higher tier, payout now"
        lines.append(f"{e['start']} to {e['end']}, peak {e['peak']:.1f} {unit}. {what}: "
                     f"Rs {e['payout']:,.0f} each to {group['members']} members "
                     f"(Rs {e['payout'] * group['members']:,.0f}).")
    for f in ahead:
        lines.append(f"Forecast {f['date']}: {f['value']:.1f} {unit}. Warn members today.")
    boto3.client("sns").publish(TopicArn=topic, Subject=f"Dihadi: {group['name']}"[:100],
                                Message="\n".join(lines))


def run_check(group, past_days):
    var = weather.PERILS[group["peril"]]["var"]
    past, upcoming = weather.fetch_recent(group["place"]["lat"], group["place"]["lon"], var,
                                          past_days=past_days, forecast_days=3)
    changed = apply_events(group, past)
    forecast = forecast_view(group, upcoming)
    try:
        notify(group, changed, forecast)
    except Exception as err:  # an email failure must not lose the recorded payouts
        print(f"notify failed for {group['id']}: {err}")
    return changed, forecast
