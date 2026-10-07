"""Orchestrator + control panel.

- POST /alert            Alertmanager webhook -> Jira ticket + GitHub redeploy
- GET  /                 control-panel UI (static/index.html)
- /api/*                 data and actions used by the UI
"""
import collections
import hmac
import logging
import math
import os
import threading
import time

import requests
from flask import Flask, jsonify, request, send_from_directory

import github_dispatch
import jira_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("orchestrator")

app = Flask(__name__, static_folder="static", static_url_path="/static")

_lock = threading.Lock()
_seen = {}            # alert fingerprint -> time a ticket was created
_last_redeploy = [0.0]
_events = collections.deque(maxlen=100)
TICKET_DEDUPE_SECONDS = 600

CHAOS_URL = os.environ.get("CHAOS_URL", "http://chaos-app:8000")
PROM_URL = os.environ.get("PROM_URL", "http://prometheus:9090")


def event(kind, message, link=None):
    """Record a timeline entry shown in the UI."""
    with _lock:
        _events.appendleft({"ts": time.time(), "kind": kind, "message": message, "link": link})


def _authorized():
    expected = os.environ.get("WEBHOOK_TOKEN", "")
    got = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    return bool(expected) and hmac.compare_digest(expected, got)


def _should_create_ticket(alert, now):
    key = alert.get("fingerprint") or alert.get("labels", {}).get("alertname", "")
    with _lock:
        if now - _seen.get(key, 0) < TICKET_DEDUPE_SECONDS:
            return False
        _seen[key] = now
        return True


def _should_redeploy(now):
    cooldown = float(os.environ.get("REDEPLOY_COOLDOWN_SECONDS", "120"))
    with _lock:
        if now - _last_redeploy[0] < cooldown:
            return False
        _last_redeploy[0] = now
        return True


def _jira_link(key):
    base = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
    return f"{base}/browse/{key}" if base and key else None


@app.get("/healthz")
def healthz():
    return "ok"


@app.post("/alert")
def alert():
    if not _authorized():
        return jsonify(error="unauthorized"), 401

    data = request.get_json(silent=True) or {}
    firing = [a for a in data.get("alerts", []) if a.get("status") == "firing"]
    now = time.time()
    tickets = []

    for a in firing:
        name = a.get("labels", {}).get("alertname")
        log.info("Alert firing: %s", name)
        event("alert", f"Alertmanager delivered alert {name}")
        if not _should_create_ticket(a, now):
            log.info("Ticket for %s already created recently; skipping", name)
            event("jira", f"Ticket for {name} already created recently; skipped")
            continue
        try:
            key = jira_client.create_issue(a)
            if key:
                log.info("Created Jira issue %s", key)
                tickets.append(key)
                event("jira", f"Created Jira issue {key} for {name}", _jira_link(key))
            else:
                log.warning("Jira not configured; skipping ticket for %s", name)
                event("jira", "Jira not configured; ticket skipped")
        except Exception as e:
            log.exception("Jira ticket creation failed for %s", name)
            event("error", f"Jira ticket creation failed: {e}")

    redeployed = False
    if firing and _should_redeploy(now):
        names = ",".join(a.get("labels", {}).get("alertname", "?") for a in firing)
        redeployed = _dispatch(names)
    elif firing:
        log.info("Redeploy skipped (cooldown)")
        event("redeploy", "Redeploy skipped (cooldown active)")

    return jsonify(firing=len(firing), tickets=tickets, redeploy_triggered=redeployed)


def _dispatch(reason):
    try:
        ok = github_dispatch.trigger_redeploy(reason=reason)
        if ok:
            log.info("Triggered redeploy workflow (reason: %s)", reason)
            event("redeploy", f"Triggered GitHub redeploy workflow ({reason})")
        else:
            log.warning("GitHub not configured; skipping redeploy")
            event("error", "GitHub not configured; redeploy skipped")
        return ok
    except Exception as e:
        log.exception("GitHub dispatch failed")
        event("error", f"GitHub dispatch failed: {e}")
        return False


# ---------------------------------------------------------------- UI + API

@app.get("/")
def ui():
    return send_from_directory(app.static_folder, "index.html")


def _prom(query):
    """Return the first sample of an instant query as float, or None."""
    try:
        r = requests.get(f"{PROM_URL}/api/v1/query", params={"query": query}, timeout=3)
        res = r.json()["data"]["result"]
        if not res:
            return None
        v = float(res[0]["value"][1])
        return None if math.isnan(v) else v
    except Exception:
        return None


def _prom_alerts():
    try:
        r = requests.get(f"{PROM_URL}/api/v1/alerts", timeout=3)
        return [
            {"name": a["labels"].get("alertname"), "state": a["state"],
             "severity": a["labels"].get("severity")}
            for a in r.json()["data"]["alerts"]
        ]
    except Exception:
        return []


def _chaos_state():
    try:
        return requests.get(f"{CHAOS_URL}/chaos/state", timeout=3).json()
    except Exception:
        return None


def _github_runs():
    repo, token = os.environ.get("GITHUB_REPO"), os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        return []
    try:
        r = requests.get(
            f"https://api.github.com/repos/{repo}/actions/runs",
            params={"per_page": 5},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            timeout=5,
        )
        return [
            {"name": w["name"], "status": w["status"], "conclusion": w["conclusion"],
             "event": w["event"], "url": w["html_url"], "created": w["created_at"]}
            for w in r.json().get("workflow_runs", [])
        ]
    except Exception:
        return []


_runs_cache = {"t": 0.0, "v": []}


def _cached_runs():
    if time.time() - _runs_cache["t"] > 5:
        _runs_cache.update(t=time.time(), v=_github_runs())
    return _runs_cache["v"]


@app.get("/api/status")
def api_status():
    with _lock:
        events = list(_events)
    return jsonify(
        now=time.time(),
        chaos=_chaos_state(),
        metrics={
            "req_rate": _prom('sum(rate(http_requests_total[15s]))'),
            "error_ratio": _prom('(sum(rate(http_requests_total{status=~"5.."}[15s])) or vector(0))'
                                 ' / sum(rate(http_requests_total[15s]))'),
            "p95": _prom('histogram_quantile(0.95, sum(rate('
                         'http_request_duration_seconds_bucket[15s])) by (le))'),
            "up": _prom('up{job="chaos-app"}'),
        },
        alerts=_prom_alerts(),
        events=events,
        runs=_cached_runs(),
        config={
            "jira": bool(os.environ.get("JIRA_API_TOKEN")),
            "github": bool(os.environ.get("GITHUB_TOKEN")),
            "jira_board": os.environ.get("JIRA_BASE_URL", "").rstrip("/"),
            "repo": os.environ.get("GITHUB_REPO", ""),
        },
    )


@app.post("/api/chaos/<action>")
def api_chaos(action):
    paths = {"fail-rate": "/chaos/fail-rate", "latency": "/chaos/latency", "reset": "/chaos/reset"}
    if action not in paths:
        return jsonify(error="unknown action"), 404
    body = request.get_json(silent=True) or {}
    try:
        r = requests.post(f"{CHAOS_URL}{paths[action]}", json=body, timeout=5)
    except Exception as e:
        return jsonify(error=str(e)), 502
    label = {"fail-rate": f"Injected failures (rate {body.get('value')})",
             "latency": f"Injected latency ({body.get('value')}s)",
             "reset": "Chaos reset manually"}[action]
    event("chaos", label)
    return jsonify(r.json())


@app.post("/api/redeploy")
def api_redeploy():
    """Manual trigger: same path as an alert-driven redeploy, bypassing the cooldown."""
    event("redeploy", "Manual redeploy requested from control panel")
    ok = _dispatch("manual-ui")
    _runs_cache["t"] = 0.0
    return jsonify(triggered=ok), (200 if ok else 502)
