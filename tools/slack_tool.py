"""Slack integration using Incoming Webhooks (free tier)."""

import os

import httpx
from dotenv import load_dotenv

load_dotenv()

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
API_CALLBACK_URL = os.getenv("API_CALLBACK_URL", "http://localhost:8000")


async def post_incident_alert(incident: dict) -> bool:
    """Post a rich Slack Block Kit incident alert message.

    Args:
        incident: Full incident report dict from the Orchestrator.

    Returns:
        True if the message was posted successfully.
    """
    incident_id = incident.get("incident_id", "unknown")
    severity = incident.get("severity", "P2")
    root_cause = incident.get("root_cause", "Unknown")
    culprit = incident.get("culprit_commit", {})
    scribe = incident.get("agent_findings", {}).get("scribe", {})
    steps = scribe.get("recommended_steps", [])
    fix_time = scribe.get("estimated_fix_time", "unknown")

    severity_emoji = {"P1": ":red_circle:", "P2": ":large_orange_circle:", "P3": ":large_yellow_circle:"}.get(severity, ":white_circle:")

    steps_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))

    culprit_sha = culprit.get("sha", "N/A") or "N/A"
    culprit_msg = culprit.get("message", "") or ""
    culprit_author = culprit.get("author", "unknown") or "unknown"

    # Block Kit JSON payload
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{severity} Incident {incident_id}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{severity_emoji} *Severity:* {severity}\n"
                    f"*Incident ID:* `{incident_id}`\n\n"
                    f"*Root Cause:*\n{root_cause}"
                ),
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Culprit Commit:* `{culprit_sha}`\n"
                    f"*Author:* {culprit_author}\n"
                    f"*Message:* _{culprit_msg}_"
                ),
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Recommended Steps:*\n{steps_text}",
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Estimated fix time: *{fix_time}*",
                }
            ],
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve Rollback", "emoji": True},
                    "style": "primary",
                    "url": f"{API_CALLBACK_URL}/approve/{incident_id}",
                    "action_id": "approve_rollback",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Dismiss", "emoji": True},
                    "style": "danger",
                    "url": f"{API_CALLBACK_URL}/dismiss/{incident_id}",
                    "action_id": "dismiss_incident",
                },
            ],
        },
        {"type": "divider"},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Investigated by *Sentinel-ES* · 3 agents · {incident.get('timestamp', '')}",
                }
            ],
        },
    ]

    payload = {"blocks": blocks}

    if not SLACK_WEBHOOK_URL or SLACK_WEBHOOK_URL.startswith("https://hooks.slack.com/services/xxx"):
        print(f"[Slack] Would post incident alert for {incident_id} (webhook not configured)")
        return False

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                SLACK_WEBHOOK_URL,
                json=payload,
                timeout=10,
            )
            return resp.status_code == 200
    except Exception as e:
        print(f"[Slack] Error posting alert: {e}")
        return False


async def post_resolution(incident_id: str, resolved_by: str) -> bool:
    """Post a follow-up message that the incident was resolved.

    Args:
        incident_id: The ID of the resolved incident.
        resolved_by: Who approved/resolved it.

    Returns:
        True if posted successfully.
    """
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":white_check_mark: *Incident {incident_id} Resolved*\n\n"
                    f"Resolved by: *{resolved_by}*\n"
                    f"Action: Rollback triggered successfully."
                ),
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "Sentinel-ES · Automated Resolution",
                }
            ],
        },
    ]

    payload = {"blocks": blocks}

    if not SLACK_WEBHOOK_URL or SLACK_WEBHOOK_URL.startswith("https://hooks.slack.com/services/xxx"):
        print(f"[Slack] Would post resolution for {incident_id} (webhook not configured)")
        return False

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                SLACK_WEBHOOK_URL,
                json=payload,
                timeout=10,
            )
            return resp.status_code == 200
    except Exception as e:
        print(f"[Slack] Error posting resolution: {e}")
        return False
