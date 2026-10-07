import jira_client
import main
import github_dispatch

ALERT = {
    "status": "firing",
    "fingerprint": "abc",
    "labels": {"alertname": "HighErrorRate", "severity": "critical"},
    "annotations": {"summary": "Error rate high", "description": "over 30%"},
    "startsAt": "2026-01-01T00:00:00Z",
}


def setup_function():
    main._seen.clear()
    main._last_redeploy[0] = 0.0


def test_jira_payload_shape():
    p = jira_client.build_payload(ALERT, "INC", "Task")["fields"]
    assert p["project"] == {"key": "INC"}
    assert p["summary"].startswith("[CRITICAL]")
    assert p["issuetype"] == {"name": "Task"}
    assert p["description"]["type"] == "doc"
    assert "HighErrorRate" in p["labels"]


def test_rejects_missing_token(monkeypatch):
    monkeypatch.setenv("WEBHOOK_TOKEN", "secret")
    r = main.app.test_client().post("/alert", json={"alerts": [ALERT]})
    assert r.status_code == 401


def test_firing_alert_creates_ticket_and_redeploys(monkeypatch):
    monkeypatch.setenv("WEBHOOK_TOKEN", "secret")
    monkeypatch.setattr(jira_client, "create_issue", lambda a: "INC-1")
    monkeypatch.setattr(github_dispatch, "trigger_redeploy", lambda reason="": True)
    r = main.app.test_client().post(
        "/alert", json={"alerts": [ALERT]}, headers={"Authorization": "Bearer secret"}
    )
    body = r.get_json()
    assert r.status_code == 200
    assert body["tickets"] == ["INC-1"]
    assert body["redeploy_triggered"] is True


def test_duplicate_alert_does_not_create_second_ticket(monkeypatch):
    monkeypatch.setenv("WEBHOOK_TOKEN", "secret")
    calls = []
    monkeypatch.setattr(jira_client, "create_issue", lambda a: calls.append(1) or "INC-1")
    monkeypatch.setattr(github_dispatch, "trigger_redeploy", lambda reason="": True)
    c = main.app.test_client()
    h = {"Authorization": "Bearer secret"}
    c.post("/alert", json={"alerts": [ALERT]}, headers=h)
    c.post("/alert", json={"alerts": [ALERT]}, headers=h)
    assert len(calls) == 1


def test_resolved_alert_is_ignored(monkeypatch):
    monkeypatch.setenv("WEBHOOK_TOKEN", "secret")
    resolved = dict(ALERT, status="resolved")
    r = main.app.test_client().post(
        "/alert", json={"alerts": [resolved]}, headers={"Authorization": "Bearer secret"}
    )
    assert r.get_json()["firing"] == 0


def test_ui_served():
    r = main.app.test_client().get("/")
    assert r.status_code == 200
    assert b"Incident Response Simulator" in r.data


def test_manual_redeploy_bypasses_cooldown_and_logs_event(monkeypatch):
    monkeypatch.setattr(github_dispatch, "trigger_redeploy", lambda reason="": True)
    main._last_redeploy[0] = 9e12  # cooldown would block an alert-driven redeploy
    c = main.app.test_client()
    assert c.post("/api/redeploy").get_json() == {"triggered": True}
    assert any(e["kind"] == "redeploy" for e in main._events)


def test_manual_redeploy_failure_returns_502(monkeypatch):
    monkeypatch.setattr(github_dispatch, "trigger_redeploy", lambda reason="": False)
    assert main.app.test_client().post("/api/redeploy").status_code == 502


def test_status_survives_unreachable_backends(monkeypatch):
    monkeypatch.setattr(main, "CHAOS_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(main, "PROM_URL", "http://127.0.0.1:1")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    body = main.app.test_client().get("/api/status").get_json()
    assert body["chaos"] is None
    assert body["alerts"] == []
    assert body["metrics"]["error_ratio"] is None


def test_chaos_proxy_rejects_unknown_action():
    assert main.app.test_client().post("/api/chaos/explode").status_code == 404


def test_incident_resets_guards_and_breaks_app(monkeypatch):
    calls = []

    class R:
        def json(self):
            return {"fail_rate": 0.8}

    def fake_post(url, json=None, timeout=0):
        calls.append((url, json))
        return R()

    monkeypatch.setattr(main.requests, "post", fake_post)
    main._seen["x"] = 1
    main._last_redeploy[0] = 9e12
    r = main.app.test_client().post("/api/incident")
    assert r.status_code == 200
    assert calls[0][0].endswith("/chaos/fail-rate") and calls[0][1] == {"value": 0.8}
    assert main._seen == {} and main._last_redeploy[0] == 0.0
