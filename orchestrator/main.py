"""Receives Alertmanager webhooks -> opens a Jira ticket and triggers a redeploy."""
import hmac
import logging
import os
import threading
import time

from flask import Flask, jsonify, request

import github_dispatch
import jira_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("orchestrator")

app = Flask(__name__)

_lock = threading.Lock()
_seen = {}            # alert fingerprint -> time a ticket was created
_last_redeploy = [0.0]
TICKET_DEDUPE_SECONDS = 600


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
        if not _should_create_ticket(a, now):
            log.info("Ticket for %s already created recently; skipping", name)
            continue
        try:
            key = jira_client.create_issue(a)
            if key:
                log.info("Created Jira issue %s", key)
                tickets.append(key)
            else:
                log.warning("Jira not configured; skipping ticket for %s", name)
        except Exception:
            log.exception("Jira ticket creation failed for %s", name)

    redeployed = False
    if firing and _should_redeploy(now):
        names = ",".join(a.get("labels", {}).get("alertname", "?") for a in firing)
        try:
            redeployed = github_dispatch.trigger_redeploy(reason=names)
            if redeployed:
                log.info("Triggered redeploy workflow (reason: %s)", names)
            else:
                log.warning("GitHub not configured; skipping redeploy")
        except Exception:
            log.exception("GitHub dispatch failed")
    elif firing:
        log.info("Redeploy skipped (cooldown)")

    return jsonify(firing=len(firing), tickets=tickets, redeploy_triggered=redeployed)
