import json
import os
import sys
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, os.path.join(HERE, ".."))
os.environ.update(AWS_DEFAULT_REGION="ap-south-1", AWS_ACCESS_KEY_ID="x",
                  AWS_SECRET_ACCESS_KEY="x", TABLE="dihadi-test")

import boto3  # noqa: E402
from moto import mock_aws  # noqa: E402

import app  # noqa: E402
import fake_weather  # noqa: E402
import messages  # noqa: E402
import monitor  # noqa: E402
import store  # noqa: E402
import weather  # noqa: E402


def call(route, body=None, gid=None, query=None):
    ev = {"routeKey": route, "body": json.dumps(body) if body is not None else None,
          "queryStringParameters": query or {}}
    if gid:
        ev["pathParameters"] = {"id": gid}
    out = app.handler(ev, None)
    return out["statusCode"], (json.loads(out["body"]) if not out.get("isBase64Encoded") else out)


@mock_aws
class ApiTests(unittest.TestCase):
    def setUp(self):
        store._table = None
        boto3.client("dynamodb").create_table(
            TableName="dihadi-test", BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                  {"AttributeName": "sk", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"},
                       {"AttributeName": "sk", "KeyType": "RANGE"}])
        os.environ["TOPIC_ARN"] = boto3.client("sns").create_topic(Name="t")["TopicArn"]
        os.environ.pop("POLLY_OFF", None)
        fake_weather.install(weather)
        self.history_calls = 0
        real = weather.fetch_history

        def counted(*a):
            self.history_calls += 1
            return real(*a)
        weather.fetch_history = counted

    def calibrate(self, **kw):
        status, cal = call("POST /calibrate", {"city": "Ahmedabad", "peril": "heat", **kw})
        self.assertEqual(status, 200, cal)
        return cal

    def test_calibration_counts_events_and_fits_warming(self):
        cal = self.calibrate()
        self.assertEqual(len(cal["years"]), 45)
        y2024 = next(y for y in cal["years"] if y["year"] == 2024)
        self.assertEqual(y2024["seq"], "121")
        self.assertGreater(cal["fit1"]["fit"]["b"], 0)
        self.assertGreater(cal["b1"], 0)
        self.assertTrue(0 < cal["fit1"]["Z"] <= 1)
        self.assertLessEqual(cal["lam2"], cal["lam1"])
        self.assertTrue(any(s["perYear"] > 0 for s in cal["suggest"]))

    def test_weather_is_cached_across_trigger_changes(self):
        self.calibrate()
        cal = self.calibrate(t1=43.0, t2=44.5)
        self.assertTrue(cal["cached"])
        self.assertEqual(self.history_calls, 1)

    def test_bad_terms(self):
        status, _ = call("POST /calibrate", {"city": "X", "peril": "heat", "t1": 45, "t2": 44})
        self.assertEqual(status, 400)
        status, _ = call("POST /calibrate", {"city": "X", "peril": "tanker"})
        self.assertEqual(status, 400)

    def _fund(self, cap=3):
        status, out = call("POST /groups", {
            "name": "Naroda site workers", "place": fake_weather.PLACE, "peril": "heat",
            "t1": 43.6, "t2": 44.1, "days": 2, "payout1": 750, "payout2": 1250, "cap": cap,
            "members": 200, "premiumPerMember": 2000, "memberShare": 0.4,
            "worstYearPayout": 750000, "pricedOn": "High emissions, 2041–60"})
        self.assertEqual(status, 201, out)
        return out["id"]

    def test_fund_records_each_event_once_with_tiers(self):
        gid = self._fund()
        status, v = call("POST /groups/{id}/check", gid=gid)
        self.assertEqual([(e["tier"], e["payout"]) for e in sorted(v["events"], key=lambda e: e["start"])],
                         [(1, 750), (2, 1250)])
        self.assertEqual(v["totals"]["paidOut"], (750 + 1250) * 200)
        self.assertEqual(v["totals"]["fromMembers"], 200 * 800)
        status, v = call("POST /groups/{id}/check", gid=gid)
        self.assertEqual(v["changed"], [])
        self.assertEqual(v["totals"]["events"], 2)

    def test_cap_stops_payouts(self):
        gid = self._fund(cap=1)
        status, v = call("POST /groups/{id}/check", gid=gid)
        self.assertEqual(v["totals"]["events"], 1)

    def test_run_split_across_checks_is_one_event_upgraded(self):
        gid = self._fund()
        group = store.get_group(gid)
        import checks
        d = ["2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04"]
        first = checks.apply_events(group, [(d[0], 44.0), (d[1], 44.0)])
        self.assertEqual([e["change"] for e in first], ["new"])
        second = checks.apply_events(group, [(d[1], 44.0), (d[2], 44.5), (d[3], 44.6)])
        self.assertEqual([e["change"] for e in second], ["upgrade"])
        ev = store.list_events(gid)
        self.assertEqual(len(ev), 1)
        self.assertEqual((ev[0]["tier"], ev[0]["payout"], ev[0]["end"]), (2, 1250, d[3]))

    def test_monitor(self):
        self._fund()
        self._fund()
        out = monitor.handler({}, None)
        self.assertEqual((out["checked"], out["failed"]), (2, 0))

    def test_member_page_and_voice(self):
        gid = self._fund()
        import checks
        group = store.get_group(gid)
        from datetime import date
        y = date.today().year
        checks.apply_events(group, [(f"{y}-05-01", 44.0), (f"{y}-05-02", 44.0)])
        status, hi = call("GET /groups/{id}/member", gid=gid, query={"lang": "hi"})
        self.assertEqual([d["state"] for d in hi["days"]], ["ok", "danger"])
        self.assertIn("1 बार लू चली", hi["speech"])
        self.assertIn("750 रुपये", hi["speech"])
        status, en = call("GET /groups/{id}/member", gid=gid, query={"lang": "en"})
        self.assertIn("Tomorrow: very hot, 45 degrees.", en["speech"])
        status, audio = call("GET /groups/{id}/speech", gid=gid, query={"lang": "hi"})
        self.assertEqual(audio["headers"]["content-type"], "audio/mpeg")
        os.environ["POLLY_OFF"] = "1"
        status, _ = call("GET /groups/{id}/speech", gid=gid, query={"lang": "hi"})
        self.assertEqual(status, 503)

    def test_simulate_route(self):
        cal = self.calibrate()
        body = {k: cal[k] for k in ("lam1", "lam2", "b1", "b2", "se1", "se2")}
        body.update(payout1=750, payout2=1250, cap=3, members=200, costOfCapital=0.06,
                    expenseRatio=0.2, memberShare=0.4, scenario="ssp245", horizon="mid")
        status, out = call("POST /simulate", body)
        self.assertEqual(status, 200, out)
        self.assertGreater(out["scenario"]["gross"], out["today"]["gross"])

    def test_unknown_fund_404(self):
        status, _ = call("GET /groups/{id}", gid="nope")
        self.assertEqual(status, 404)


class RupeeTests(unittest.TestCase):
    def test_indian_grouping(self):
        self.assertEqual(messages._rupees(1250000), "12,50,000")
        self.assertEqual(messages._rupees(123456789), "12,34,56,789")


if __name__ == "__main__":
    unittest.main()
