"""Run all of Dihadi on your laptop: http://localhost:8000

Serves the frontend and sends /api/* to the same Lambda code that runs on
AWS. DynamoDB and SNS are replaced by in-memory fakes (moto), so nothing
needs an AWS account. Data is lost when you stop the server.

  python backend/local_server.py                 real weather (needs internet)
  python backend/local_server.py --fake-weather  fixed sample weather, works offline

Extra local-only route:
  POST /api/_monitor   runs the 9 am daily check right now
"""
import base64
import json
import mimetypes
import urllib.parse
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
FRONTEND = os.path.join(HERE, "..", "frontend")
sys.path.insert(0, os.path.join(HERE, "src"))
os.environ.update(AWS_DEFAULT_REGION="ap-south-1", AWS_ACCESS_KEY_ID="local",
                  AWS_SECRET_ACCESS_KEY="local", TABLE="dihadi-local")
# No real Polly offline: the member page falls back to the browser's own voice.
os.environ["POLLY_OFF"] = "1"

try:
    from moto import mock_aws
except ImportError as err:
    sys.exit(f"Could not import moto ({err}).\n"
             f"This Python is {sys.executable}\n"
             "Install the packages into this same Python with:\n"
             "  python -m pip install -r backend/requirements-dev.txt")

mock = mock_aws()
mock.start()

import boto3  # noqa: E402
import app  # noqa: E402
import monitor  # noqa: E402
import weather  # noqa: E402

boto3.client("dynamodb").create_table(
    TableName="dihadi-local", BillingMode="PAY_PER_REQUEST",
    AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                          {"AttributeName": "sk", "AttributeType": "S"}],
    KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"},
               {"AttributeName": "sk", "KeyType": "RANGE"}])
os.environ["TOPIC_ARN"] = boto3.client("sns").create_topic(Name="dihadi-local")["TopicArn"]

if "--fake-weather" in sys.argv:
    sys.path.insert(0, HERE)
    import fake_weather  # noqa: E402
    fake_weather.install(weather)
    print("Using fixed sample weather (offline). Numbers are not real.")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("  " + self.command, self.path, "->", args[1] if len(args) > 1 else "")

    def _send(self, status, body, ctype):
        self.send_response(status)
        self.send_header("content-type", ctype)
        self.send_header("access-control-allow-origin", "*")
        self.send_header("access-control-allow-headers", "content-type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain")

    def _api(self, method, path):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n).decode() if n else None
        if method == "POST" and path == "/_monitor":
            return self._send(200, json.dumps(monitor.handler({}, None)).encode(), "application/json")
        parts = path.strip("/").split("/")
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
        event = {"routeKey": f"{method} {path}", "body": body, "queryStringParameters": query}
        if parts[0] == "groups" and len(parts) >= 2:
            event["pathParameters"] = {"id": parts[1]}
            event["routeKey"] = f"{method} /groups/{{id}}" + ("/" + parts[2] if len(parts) == 3 else "")
        out = app.handler(event, None)
        payload = base64.b64decode(out["body"]) if out.get("isBase64Encoded") else out["body"].encode()
        self._send(out["statusCode"], payload, out["headers"].get("content-type", "application/json"))

    def _route(self, method):
        path = self.path.split("?")[0]
        if path.startswith("/api/"):
            return self._api(method, path[4:])
        name = path.lstrip("/") or "index.html"
        file = os.path.normpath(os.path.join(FRONTEND, name))
        if not file.startswith(os.path.normpath(FRONTEND)) or not os.path.isfile(file):
            return self._send(404, b"Not found", "text/plain")
        with open(file, "rb") as f:
            self._send(200, f.read(), (mimetypes.guess_type(file)[0] or "text/plain") + "; charset=utf-8")

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"Dihadi running at http://localhost:{port}  (Ctrl+C to stop)")
    try:
        ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
