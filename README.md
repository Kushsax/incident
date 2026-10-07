# Incident-Response Simulator

Closed loop: **fault → Prometheus detects → Alertmanager alerts → orchestrator opens a Jira ticket and triggers a GitHub Actions redeploy → self-hosted runner recreates the container → Grafana shows recovery.**

```
chaos-app ──metrics──► Prometheus ──alert──► Alertmanager ──webhook──► orchestrator
    ▲                      │                                              │      │
 loadgen                Grafana                                     Jira API   GitHub repository_dispatch
                                                                                  │
                                              self-hosted runner: docker compose up --force-recreate chaos-app
```

## Ports
| URL | What |
|---|---|
| http://localhost:8000 | chaos-app (`/work`, `/metrics`, `/chaos/*`) |
| http://localhost:3000 | Grafana (dashboard "Chaos App"; admin / admin) |
| http://localhost:9090 | Prometheus (Alerts tab) |
| http://localhost:9093 | Alertmanager |
| http://localhost:9000 | orchestrator (`/healthz`) |

## Setup
1. Install Docker Desktop and start it.
2. `cp .env.example .env` and fill in the values (see below). Without Jira/GitHub values the stack still runs; the orchestrator just logs "not configured; skipping".
3. `docker compose up -d --build`
4. Create a GitHub repo, push this folder to it (`git init`, `git remote add origin ...`, `git push`).
5. Register the self-hosted runner: repo → Settings → Actions → Runners → New self-hosted runner (macOS), run the shown commands, then `./run.sh` and keep it running.
6. Jira: free site at atlassian.com, create a project with key `INC`, create an API token at https://id.atlassian.com/manage-profile/security/api-tokens.
7. GitHub token: Settings → Developer settings → Fine-grained tokens → this repo only → Repository permission **Contents: Read and write** (needed for `repository_dispatch`).
8. Put `JIRA_*`, `GITHUB_REPO` (`user/repo`) and `GITHUB_TOKEN` in `.env`, then `docker compose up -d --force-recreate orchestrator`.

## Demo script
```bash
# 1. healthy baseline: open Grafana http://localhost:3000 (error ratio ~0)
# 2. inject failures
curl -X POST localhost:8000/chaos/fail-rate -H 'Content-Type: application/json' -d '{"value":0.8}'
# 3. watch: Grafana error ratio rises -> Prometheus Alerts: HighErrorRate pending -> firing (~30s)
#    -> Alertmanager shows it -> docker compose logs -f orchestrator
#    -> new ticket in Jira -> GitHub Actions tab: "Redeploy chaos-app" runs
#    -> container recreated, chaos resets, Grafana recovers
# Latency variant:
curl -X POST localhost:8000/chaos/latency -H 'Content-Type: application/json' -d '{"value":2,"rate":1}'
# Manual reset any time:
curl -X POST localhost:8000/chaos/reset
```
Note: the orchestrator waits out a 120 s redeploy cooldown and won't create a duplicate Jira ticket for the same alert for 10 minutes. Restart it (`docker compose restart orchestrator`) to repeat the demo quickly.

## Tests
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
(cd app && ../.venv/bin/python -m pytest -q)
(cd orchestrator && ../.venv/bin/python -m pytest -q)
```
CI (`.github/workflows/ci.yml`) runs flake8, both test suites and the Docker builds on every push.

## Troubleshooting
- Redeploy workflow stays "queued": the runner isn't running (`./run.sh`).
- Dispatch 404/403: token lacks Contents write, or `GITHUB_REPO` is wrong.
- Jira 400: wrong `JIRA_ISSUE_TYPE` for your project (try `Task`).
- Alert never fires: loadgen produces the traffic; check `docker compose ps`.
