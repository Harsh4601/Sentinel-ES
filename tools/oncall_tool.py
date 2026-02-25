"""On-call routing using a simple JSON schedule file."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

SCHEDULE_FILE = os.path.join(os.path.dirname(__file__), "..", "oncall_schedule.json")

DEFAULT_SCHEDULE = [
    {"name": "Alice Chen", "slack_id": "@alice", "role": "primary", "start": "Mon", "end": "Wed"},
    {"name": "Bob Martinez", "slack_id": "@bob", "role": "primary", "start": "Thu", "end": "Fri"},
    {"name": "Carol Singh", "slack_id": "@carol", "role": "primary", "start": "Sat", "end": "Sun"},
    {"name": "Dave Kim", "slack_id": "@dave", "role": "secondary", "start": "Mon", "end": "Sun"},
    {"name": "Eve Johnson", "slack_id": "@eve", "role": "manager", "start": "Mon", "end": "Sun"},
]

DAY_MAP = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}


def _load_schedule() -> list[dict]:
    """Load the on-call schedule from JSON file, falling back to defaults."""
    if os.path.exists(SCHEDULE_FILE):
        try:
            with open(SCHEDULE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return DEFAULT_SCHEDULE


def _is_on_day(entry: dict, day_num: int) -> bool:
    """Check if an on-call entry covers the given day of the week."""
    start = DAY_MAP.get(entry.get("start", ""), 0)
    end = DAY_MAP.get(entry.get("end", ""), 6)
    if start <= end:
        return start <= day_num <= end
    return day_num >= start or day_num <= end


def get_current_oncall() -> dict:
    """Return who is currently the primary on-call based on day of week.

    Returns:
        Dict with name, slack_id, and role of the current primary on-call.
    """
    schedule = _load_schedule()
    today = datetime.now(timezone.utc).weekday()

    for entry in schedule:
        if entry.get("role") == "primary" and _is_on_day(entry, today):
            return entry

    return {"name": "Unknown", "slack_id": "@oncall", "role": "primary"}


def get_escalation_chain() -> list[dict]:
    """Return the full escalation chain: primary, secondary, manager.

    Returns:
        Ordered list of on-call personnel for the current day.
    """
    schedule = _load_schedule()
    today = datetime.now(timezone.utc).weekday()

    chain = []
    for role in ("primary", "secondary", "manager"):
        for entry in schedule:
            if entry.get("role") == role and _is_on_day(entry, today):
                chain.append(entry)
                break

    return chain


def format_oncall_mention() -> str:
    """Format a Slack mention string for the current primary on-call.

    Returns:
        String like "@alice you are on-call. Approve to rollback."
    """
    oncall = get_current_oncall()
    return f"{oncall['slack_id']} you are on-call. Approve to rollback."
