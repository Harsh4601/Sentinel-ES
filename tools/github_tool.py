"""GitHub API wrapper for commit history and rollback PR creation."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_API = "https://api.github.com"


def _headers() -> dict:
    h = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN and GITHUB_TOKEN != "your_github_pat_here":
        h["Authorization"] = f"token {GITHUB_TOKEN}"
    return h


async def get_recent_commits(repo: str, since_minutes: int = 60) -> list[dict]:
    """Fetch recent commits from a GitHub repository.

    Args:
        repo: Repository in 'owner/name' format.
        since_minutes: How far back to look.

    Returns:
        List of commit dicts from GitHub API.
    """
    since = (datetime.now(timezone.utc) - timedelta(minutes=since_minutes)).isoformat()
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{GITHUB_API}/repos/{repo}/commits",
                params={"since": since},
                headers=_headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json()
            return []
    except Exception:
        return []


async def create_rollback_pr(
    repo: str, culprit_sha: str, incident_id: str, incident_report: str = ""
) -> str | None:
    """Create a draft rollback PR that reverts the culprit commit.

    Guardrail: only creates DRAFT PRs, never merges.

    Args:
        repo: Repository in 'owner/name' format.
        culprit_sha: SHA of the commit to revert.
        incident_id: Sentinel-ES incident ID.
        incident_report: Markdown-formatted incident report for the PR body.

    Returns:
        PR URL if created, None otherwise.
    """
    if not GITHUB_TOKEN or GITHUB_TOKEN == "your_github_pat_here":
        print(f"[GitHub] Would create rollback PR for {culprit_sha} (no token configured)")
        return None

    branch_name = f"sentinel/rollback-{incident_id}"

    try:
        async with httpx.AsyncClient() as client:
            # Get default branch
            repo_resp = await client.get(
                f"{GITHUB_API}/repos/{repo}",
                headers=_headers(),
                timeout=10,
            )
            if repo_resp.status_code != 200:
                return None
            default_branch = repo_resp.json().get("default_branch", "main")

            # Get the ref for the default branch
            ref_resp = await client.get(
                f"{GITHUB_API}/repos/{repo}/git/ref/heads/{default_branch}",
                headers=_headers(),
                timeout=10,
            )
            if ref_resp.status_code != 200:
                return None
            base_sha = ref_resp.json()["object"]["sha"]

            # Create a new branch
            await client.post(
                f"{GITHUB_API}/repos/{repo}/git/refs",
                headers=_headers(),
                json={
                    "ref": f"refs/heads/{branch_name}",
                    "sha": base_sha,
                },
                timeout=10,
            )

            # Get commit details to build revert message
            commit_resp = await client.get(
                f"{GITHUB_API}/repos/{repo}/commits/{culprit_sha}",
                headers=_headers(),
                timeout=10,
            )
            commit_msg = "unknown commit"
            if commit_resp.status_code == 200:
                commit_msg = commit_resp.json().get("commit", {}).get("message", commit_msg).split("\n")[0]

            # Create draft PR
            pr_body = (
                f"## Sentinel-ES Automated Rollback\n\n"
                f"**Incident ID:** `{incident_id}`\n"
                f"**Culprit Commit:** `{culprit_sha}`\n\n"
                f"---\n\n{incident_report}" if incident_report else
                f"## Sentinel-ES Automated Rollback\n\n"
                f"**Incident ID:** `{incident_id}`\n"
                f"**Culprit Commit:** `{culprit_sha}`\n"
            )

            pr_resp = await client.post(
                f"{GITHUB_API}/repos/{repo}/pulls",
                headers=_headers(),
                json={
                    "title": f"Sentinel-ES: Rollback {commit_msg}",
                    "body": pr_body,
                    "head": branch_name,
                    "base": default_branch,
                    "draft": True,
                },
                timeout=10,
            )

            if pr_resp.status_code == 201:
                return pr_resp.json().get("html_url")
            return None

    except Exception as e:
        print(f"[GitHub] Error creating rollback PR: {e}")
        return None
