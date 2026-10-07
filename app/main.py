"""Chaos app: a tiny service whose failures we can dial up on demand."""
import time

from flask import Flask, g, jsonify, request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from chaos import Chaos

app = Flask(__name__)
chaos = Chaos()

REQUESTS = Counter("http_requests_total", "HTTP requests", ["endpoint", "status"])
LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["endpoint"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
)

UNTRACKED = ("/metrics", "/chaos", "/healthz")


@app.before_request
def _start_timer():
    g.start = time.perf_counter()


@app.after_request
def _record(resp):
    if not request.path.startswith(UNTRACKED):
        endpoint = request.path
        REQUESTS.labels(endpoint, str(resp.status_code)).inc()
        LATENCY.labels(endpoint).observe(time.perf_counter() - g.start)
    return resp


@app.get("/")
def index():
    return jsonify(service="chaos-app", status="ok", chaos=chaos.state())


@app.get("/healthz")
def healthz():
    return "ok"


@app.get("/work")
def work():
    if chaos.apply():
        return jsonify(error="injected failure"), 500
    return jsonify(result="done")


@app.get("/chaos/state")
def chaos_state():
    return jsonify(chaos.state())


@app.post("/chaos/fail-rate")
def chaos_fail_rate():
    body = request.get_json(silent=True) or {}
    chaos.set_fail_rate(body.get("value", 0.0))
    return jsonify(chaos.state())


@app.post("/chaos/latency")
def chaos_latency():
    body = request.get_json(silent=True) or {}
    chaos.set_latency(body.get("value", 0.0), body.get("rate", 1.0))
    return jsonify(chaos.state())


@app.post("/chaos/reset")
def chaos_reset():
    chaos.reset()
    return jsonify(chaos.state())


@app.get("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
