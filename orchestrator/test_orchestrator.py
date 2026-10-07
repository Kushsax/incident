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
