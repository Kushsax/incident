"""Creates incident tickets in Jira Cloud via the REST API v3."""
import os

import requests


def build_payload(alert, project_key, issue_type="Task"):
    labels = alert.get("labels", {})
    ann = alert.get("annotations", {})
    name = labels.get("alertname", "UnknownAlert")
    severity = labels.get("severity", "unknown")
    summary = ann.get("summary") or f"{name} firing"

    lines = [
        f"Alert: {name}",
        f"Severity: {severity}",
        f"Started at: {alert.get('startsAt', 'n/a')}",
        f"Description: {ann.get('description', 'n/a')}",
        "Labels: " + ", ".join(f"{k}={v}" for k, v in sorted(labels.items())),
        "Auto-remediation: redeploy of chaos-app triggered via GitHub Actions.",
    ]
    description = {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": line}]} for line in lines
        ],
    }
    return {
        "fields": {
            "project": {"key": project_key},
            "summary": f"[{severity.upper()}] {summary}"[:250],
            "description": description,
            "issuetype": {"name": issue_type},
            "labels": ["incident", "auto-generated", name],
        }
    }


def create_issue(alert):
    base = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_API_TOKEN")
    project = os.environ.get("JIRA_PROJECT_KEY")
    if not all([base, email, token, project]):
        return None  # not configured; caller logs a skip
    payload = build_payload(alert, project, os.environ.get("JIRA_ISSUE_TYPE", "Task"))
    resp = requests.post(
        f"{base}/rest/api/3/issue", json=payload, auth=(email, token), timeout=15
    )
    resp.raise_for_status()
    return resp.json().get("key")
