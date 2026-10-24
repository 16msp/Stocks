"""Start the daily price update (the GitHub Actions workflow) from the app, and show how its last runs went.

GitHub's own schedule is often hours late or skipped, so the stored prices can lag. A person pressing the button is the only thing that starts a run from here.
Needs a GitHub token in the settings (GITHUB_DISPATCH_TOKEN): a fine-grained token for this one repository with the "Actions: Read and write" permission and nothing else.
The token is never shown or logged.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

from strategies.config import get_setting

DEFAULT_REPO = "16msp/Stocks"
WORKFLOW_FILE = "daily_prices.yml"
BRANCH = "main"
API = "https://api.github.com"
IST = timezone(timedelta(hours=5, minutes=30))
ACTIVE = {"queued", "in_progress", "waiting", "pending", "requested"}


class JobError(Exception):
    """A message that is safe to show the user (never contains the token)."""


def _repo() -> str:
    return get_setting("GITHUB_REPO", DEFAULT_REPO) or DEFAULT_REPO


def configured() -> bool:
    return bool(get_setting("GITHUB_DISPATCH_TOKEN"))


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_setting('GITHUB_DISPATCH_TOKEN')}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


def _explain(resp: requests.Response, action: str) -> JobError:
    hint = {
        401: "GitHub did not accept the token (wrong, expired or revoked).",
        403: "The token is not allowed to do that: it needs the 'Actions: Read and write' permission on this repository.",
        404: "GitHub could not find the workflow or the repository, or the token has no access to it.",
        422: f"GitHub refused to start the workflow on branch '{BRANCH}'.",
    }.get(resp.status_code, f"GitHub answered with status {resp.status_code}.")
    return JobError(f"Could not {action}: {hint}")


def recent_runs(limit: int = 5) -> list[dict]:
    """The latest runs of the price workflow, newest first: status (queued / in_progress / completed), conclusion, started (IST text), event, url."""
    try:
        resp = requests.get(f"{API}/repos/{_repo()}/actions/workflows/{WORKFLOW_FILE}/runs", headers=_headers(), params={"per_page": limit}, timeout=15)
    except requests.RequestException:
        raise JobError("Could not reach GitHub to read the latest runs.") from None
    if resp.status_code != 200:
        raise _explain(resp, "read the latest runs")
    out = []
    for r in resp.json().get("workflow_runs", []):
        created = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
        out.append({"status": r["status"], "conclusion": r.get("conclusion"), "event": r["event"], "created": created, "started": created.astimezone(IST).strftime("%d %b %H:%M IST"),
                    "url": r["html_url"]})
    return out


def run_now() -> str:
    """Start one run of the price workflow. Refuses while a run is already waiting or running (the workflow never runs two at once, so a second click would only queue)."""
    runs = recent_runs(5)
    active = [r for r in runs if r["status"] in ACTIVE]
    if active:
        raise JobError(f"A price update is already {'running' if active[0]['status'] == 'in_progress' else 'waiting to start'} (started {active[0]['started']}). Wait for it to finish.")
    try:
        resp = requests.post(f"{API}/repos/{_repo()}/actions/workflows/{WORKFLOW_FILE}/dispatches", headers=_headers(), json={"ref": BRANCH}, timeout=15)
    except requests.RequestException:
        raise JobError("Could not reach GitHub to start the update.") from None
    if resp.status_code != 204:
        raise _explain(resp, "start the update")
    return "Price update started on GitHub. It takes a few minutes: press Recalculate afterwards."


def describe(run: dict) -> str:
    if run["status"] in ACTIVE:
        return "⏳ running" if run["status"] == "in_progress" else "⏳ waiting to start"
    return {"success": "✅ finished", "failure": "❌ failed", "cancelled": "⚪ cancelled"}.get(run["conclusion"], f"⚪ {run['conclusion'] or run['status']}")
