"""DynamoDB access. One table, keyed by pk/sk:

  GROUP#<id> / META            the fund: place, trigger, payout, members, premium
  GROUP#<id> / EVENT#<start>   a heatwave or rain spell that triggered a payout
  SERIES#<lat>#<lon>#<var> / CACHE   45 years of daily weather, kept for 30 days
"""
import json
import os
import secrets
import time
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

_table = None


def table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(os.environ["TABLE"])
    return _table


def _to_db(o):
    if isinstance(o, float):
        return Decimal(str(o))
    if isinstance(o, dict):
        return {k: _to_db(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_to_db(v) for v in o]
    return o


def _from_db(o):
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    if isinstance(o, dict):
        return {k: _from_db(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_from_db(v) for v in o]
    return o


def cache_get(key):
    item = table().get_item(Key={"pk": key, "sk": "CACHE"}).get("Item")
    if item and int(item.get("ttl", 0)) > time.time():
        return json.loads(item["data"])
    return None


def cache_put(key, data, days=30):
    table().put_item(Item={"pk": key, "sk": "CACHE", "data": json.dumps(data),
                           "ttl": int(time.time() + days * 86400)})


def create_group(fields):
    gid = secrets.token_urlsafe(6)
    item = {"pk": f"GROUP#{gid}", "sk": "META", "id": gid,
            "createdAt": int(time.time()), **fields}
    table().put_item(Item=_to_db(item))
    return gid


def get_group(gid):
    item = table().get_item(Key={"pk": f"GROUP#{gid}", "sk": "META"}).get("Item")
    return _from_db(item) if item else None


def list_groups():
    out, kwargs = [], {"FilterExpression": Attr("sk").eq("META") & Attr("pk").begins_with("GROUP#")}
    while True:
        page = table().scan(**kwargs)
        out.extend(_from_db(i) for i in page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            return out
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def put_event(gid, ev):
    """Record a new payout event. Returns False if one already starts that day."""
    try:
        table().put_item(
            Item=_to_db({"pk": f"GROUP#{gid}", "sk": f"EVENT#{ev['start']}", **ev,
                         "recordedAt": int(time.time())}),
            ConditionExpression="attribute_not_exists(sk)",
        )
        return True
    except ClientError as err:
        if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def update_event(gid, start, fields):
    names = {f"#{k}": k for k in fields}
    values = {f":{k}": v for k, v in _to_db(fields).items()}
    table().update_item(
        Key={"pk": f"GROUP#{gid}", "sk": f"EVENT#{start}"},
        UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in fields),
        ExpressionAttributeNames=names, ExpressionAttributeValues=values,
    )


def list_events(gid):
    """Newest first."""
    resp = table().query(KeyConditionExpression=Key("pk").eq(f"GROUP#{gid}") & Key("sk").begins_with("EVENT#"),
                         ScanIndexForward=False)
    return [_from_db(i) for i in resp.get("Items", [])]
