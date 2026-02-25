"""Safety guardrails for Sentinel-ES — human-in-the-loop enforcement."""

from __future__ import annotations

import functools
import os
import re
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

APPROVAL_MODE = os.getenv("SENTINEL_APPROVAL_MODE", "strict")

SAFE_ACTIONS = frozenset([
    "read_es_query",
    "esql_select",
    "slack_notification",
    "create_draft_pr",
    "search_runbooks",
    "list_incidents",
    "health_check",
    "activity_log",
])

DANGEROUS_PATTERNS = frozenset([
    "production",
    "deploy",
    "rollback_execute",
    "db_write",
    "secret_rotation",
    "delete_index",
    "force_merge",
    "cluster_settings",
])

UNSAFE_ESQL_KEYWORDS = re.compile(
    r"\b(DELETE|UPDATE|DROP|PUT)\b",
    re.IGNORECASE,
)


class ApprovalRequiredError(Exception):
    """Raised when an action requires human approval before proceeding."""

    def __init__(self, action: str, details: str = ""):
        self.action = action
        self.details = details
        super().__init__(f"Approval required for action: {action}. {details}")


class ActionGuardrail:
    """Validates actions against safety policies before execution."""

    @staticmethod
    def is_safe_to_auto_execute(action: str) -> bool:
        """Check if an action can be auto-executed without human approval.

        Returns True ONLY for: read-only ES queries, Slack notifications,
        creating draft PRs, searching runbooks.
        Returns False for: anything touching production, deployments,
        DB writes, secret rotation.
        """
        action_lower = action.lower().strip()

        if action_lower in SAFE_ACTIONS:
            return True

        for pattern in DANGEROUS_PATTERNS:
            if pattern in action_lower:
                return False

        return False

    @staticmethod
    def requires_human_approval(incident: dict) -> bool:
        """Determine if an incident requires human approval before action.

        Always True for P1 severity.
        True if rollback_possible is True.
        False only for P3 with low confidence.
        """
        severity = incident.get("severity", "P2")
        confidence = incident.get("agent_findings", {}).get("sleuth", {}).get("confidence", "medium")
        scribe = incident.get("agent_findings", {}).get("scribe", {})
        rollback_possible = scribe.get("rollback_possible", False)

        if severity == "P1":
            return True

        if rollback_possible:
            return True

        if severity == "P3" and confidence == "low":
            return False

        return True

    @staticmethod
    def validate_esql_query(query: str) -> tuple[bool, str]:
        """Validate that an ES|QL query is safe to execute.

        Rejects queries containing: DELETE, UPDATE, DROP, PUT mapping changes.

        Returns:
            Tuple of (is_safe, reason).
        """
        if not query or not query.strip():
            return False, "Empty query"

        match = UNSAFE_ESQL_KEYWORDS.search(query)
        if match:
            keyword = match.group(0).upper()
            return False, f"Query contains unsafe keyword: {keyword}"

        return True, "Query is safe"


def require_approval(func):
    """Decorator that enforces human approval for state-modifying functions.

    In 'strict' mode (default): raises ApprovalRequiredError.
    In 'auto' mode (testing only): logs a warning and proceeds.
    """
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        mode = os.getenv("SENTINEL_APPROVAL_MODE", "strict")
        action_name = func.__qualname__

        if mode == "strict":
            raise ApprovalRequiredError(
                action=action_name,
                details=f"Set SENTINEL_APPROVAL_MODE=auto to bypass (testing only).",
            )
        else:
            print(
                f"[GUARDRAIL WARNING] Auto-executing {action_name} "
                f"(SENTINEL_APPROVAL_MODE={mode}). "
                f"This should only happen in testing."
            )

        return await func(*args, **kwargs)

    return wrapper
