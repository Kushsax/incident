"""Triggers the redeploy workflow through GitHub's repository_dispatch API."""
import os

import requests


def trigger_redeploy(reason=""):
    repo = os.environ.get("GITHUB_REPO")
    token = os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        return False  # not configured; caller logs a skip
    resp = requests.post(
        f"https://api.github.com/repos/{repo}/dispatches",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"event_type": "redeploy", "client_payload": {"reason": reason}},
        timeout=15,
    )
    resp.raise_for_status()  # GitHub returns 204 on success
    return True
