"""Historian Agent — Git commit investigator that finds culprit deployments."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv

from agents.base_agent import BaseAgent

load_dotenv()

HISTORIAN_SYSTEM_PROMPT = """You are a git archaeologist. Given recent commits and an error description, you identify \
which commit most likely introduced the bug. Be precise about PR numbers and authors.

Always respond with valid JSON in this exact format:
{
  "culprit_commit_sha": "<full or short SHA>",
  "culprit_commit_message": "<commit message>",
  "author": "<author name>",
  "pr_number": "<PR number or null>",
  "reasoning": "<why this commit is the likely culprit>",
  "confidence": "high|medium|low"
}"""

MOCK_COMMITS = [
    {
        "sha": "a1b2c3d4e5f6789012345678",
        "commit": {
            "author": {"name": "Alice Chen", "date": (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()},
            "message": "fix: update database connection pool settings (#142)\n\nChanged max_pool_size from 10 to 5 to reduce memory usage",
        },
        "html_url": "https://github.com/example/repo/commit/a1b2c3d",
    },
    {
        "sha": "b2c3d4e5f67890123456789a",
        "commit": {
            "author": {"name": "Bob Martinez", "date": (datetime.now(timezone.utc) - timedelta(minutes=35)).isoformat()},
            "message": "feat: add new payment processing endpoint (#143)\n\nAdded /api/v2/checkout with new validation logic",
        },
        "html_url": "https://github.com/example/repo/commit/b2c3d4e",
    },
    {
        "sha": "c3d4e5f678901234567890ab",
        "commit": {
            "author": {"name": "Carol Singh", "date": (datetime.now(timezone.utc) - timedelta(minutes=28)).isoformat()},
            "message": "refactor: migrate auth service to async handlers (#144)\n\nConverted all sync DB calls to async, updated connection handling",
        },
        "html_url": "https://github.com/example/repo/commit/c3d4e5f",
    },
    {
        "sha": "d4e5f6789012345678901bcd",
        "commit": {
            "author": {"name": "Dave Kim", "date": (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()},
            "message": "chore: bump dependencies and update CI config (#145)",
        },
        "html_url": "https://github.com/example/repo/commit/d4e5f67",
    },
    {
        "sha": "e5f67890123456789012cdef",
        "commit": {
            "author": {"name": "Eve Johnson", "date": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()},
            "message": "docs: update API documentation for v2 endpoints (#146)",
        },
        "html_url": "https://github.com/example/repo/commit/e5f6789",
    },
]


class HistorianAgent(BaseAgent):
    """Investigates recent git commits to find the one that likely caused an error."""

    def __init__(self):
        super().__init__(
            name="Historian",
            system_prompt=HISTORIAN_SYSTEM_PROMPT,
        )

    async def find_culprit_commit(
        self,
        error_summary: str,
        repo: str | None = None,
        since_minutes: int = 60,
    ) -> dict:
        """Analyze recent commits against an error summary to find the culprit.

        Args:
            error_summary: Description of the error pattern from the Sleuth agent.
            repo: GitHub repo in 'owner/name' format. Falls back to GITHUB_REPO env var.
            since_minutes: How far back to search for commits.

        Returns:
            Dict with culprit_commit_sha, author, reasoning, confidence, etc.
        """
        self.log_activity("find_culprit:start", f"Searching commits for: {error_summary[:80]}")

        repo = repo or os.getenv("GITHUB_REPO", "")
        commits = await self._fetch_commits(repo, since_minutes)

        if not commits:
            self.log_activity("find_culprit:no_commits", "No recent commits found")
            return {
                "culprit_commit_sha": None,
                "culprit_commit_message": "No recent commits found",
                "author": "unknown",
                "pr_number": None,
                "reasoning": "No commits in the time window to analyze",
                "confidence": "low",
            }

        commits_summary = "\n".join(
            f"- SHA: {c['sha'][:8]} | Author: {c['commit']['author']['name']} | "
            f"Date: {c['commit']['author']['date']} | Message: {c['commit']['message'].split(chr(10))[0]}"
            for c in commits
        )

        prompt = (
            f"Error summary: {error_summary}\n\n"
            f"Recent commits:\n{commits_summary}\n\n"
            "Given this error, which of these commits is most likely the culprit? "
            "Explain why. Respond with JSON only."
        )

        response_text = await self.run(prompt)
        findings = self._parse_findings(response_text, commits)

        self.memory["last_investigation"] = findings
        self.log_activity("find_culprit:complete", f"Culprit: {findings['culprit_commit_sha']}")

        return findings

    async def _fetch_commits(self, repo: str, since_minutes: int) -> list[dict]:
        """Fetch recent commits from GitHub API, falling back to mock data."""
        github_token = os.getenv("GITHUB_TOKEN", "")

        if not repo or not github_token or github_token == "your_github_pat_here":
            self.log_activity("fetch_commits:mock", "Using mock commit data (no GitHub credentials)")
            return MOCK_COMMITS

        since = (datetime.now(timezone.utc) - timedelta(minutes=since_minutes)).isoformat()
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"token {github_token}",
        }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"https://api.github.com/repos/{repo}/commits",
                    params={"since": since},
                    headers=headers,
                    timeout=10,
                )
                if resp.status_code == 200:
                    return resp.json()
                self.log_activity("fetch_commits:api_error", f"GitHub API returned {resp.status_code}")
        except Exception as e:
            self.log_activity("fetch_commits:error", f"GitHub API error: {e}")

        return MOCK_COMMITS

    def _parse_findings(self, response_text: str, commits: list[dict]) -> dict:
        """Parse LLM response into structured findings."""
        try:
            cleaned = response_text.strip()
            if "```json" in cleaned:
                cleaned = cleaned.split("```json")[1].split("```")[0].strip()
            elif "```" in cleaned:
                cleaned = cleaned.split("```")[1].split("```")[0].strip()
            data = json.loads(cleaned)

            pr_num = data.get("pr_number")
            if isinstance(pr_num, str) and pr_num.lower() in ("null", "none", ""):
                pr_num = None

            return {
                "culprit_commit_sha": data.get("culprit_commit_sha", "unknown"),
                "culprit_commit_message": data.get("culprit_commit_message", ""),
                "author": data.get("author", "unknown"),
                "pr_number": pr_num,
                "reasoning": data.get("reasoning", ""),
                "confidence": data.get("confidence", "medium"),
            }
        except (json.JSONDecodeError, IndexError):
            return {
                "culprit_commit_sha": commits[0]["sha"][:8] if commits else "unknown",
                "culprit_commit_message": response_text[:200],
                "author": "unknown",
                "pr_number": None,
                "reasoning": response_text,
                "confidence": "low",
            }
