"""Elastic Agent Builder configuration generator for Sentinel-ES."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

API_CALLBACK_URL = os.getenv("API_CALLBACK_URL", "http://localhost:8000")

AGENT_CONFIG = {
    "metadata": {
        "name": "Sentinel-ES SRE Agent",
        "description": "Autonomous incident detection and remediation orchestrator. "
                       "Detects anomalies via ES|QL, coordinates specialist sub-agents, "
                       "and produces actionable incident reports with human-in-the-loop approval.",
        "version": "0.1.0",
        "author": "Sentinel-ES Team",
        "model": "gpt-4o-mini",
        "model_alternatives": ["claude-haiku", "llama3-8b-8192"],
        "tags": ["sre", "incident-response", "observability", "elasticsearch"],
    },
    "system_prompt": (
        "You are Sentinel-ES, an autonomous SRE agent. When called, you:\n"
        "1. First detect if there is an active anomaly using detect_anomalies_tool\n"
        "2. If anomaly found, run the full investigation pipeline:\n"
        "   a. Use query_esql_tool to analyze error patterns\n"
        "   b. Use search_runbooks_tool to find relevant remediation procedures\n"
        "   c. Use get_recent_commits_tool to identify potential culprit deployments\n"
        "3. Synthesize findings from all tools into a unified incident report\n"
        "4. Always require human approval before any write actions\n"
        "5. Post the incident report to Slack using post_slack_alert_tool\n"
        "6. Explain your reasoning at each step\n\n"
        "Safety rules:\n"
        "- NEVER execute destructive operations without explicit human approval\n"
        "- Only create DRAFT pull requests, never merge\n"
        "- Always validate ES|QL queries before execution\n"
        "- Log all actions for audit trail"
    ),
    "tools": [
        {
            "name": "detect_anomalies_tool",
            "description": "Detect HTTP 500 anomalies by comparing recent error rates against a baseline. "
                           "Calls the Sentinel-ES API to run ES|QL anomaly detection.",
            "type": "api",
            "api": {
                "method": "POST",
                "url": f"{API_CALLBACK_URL}/webhook/alert",
                "headers": {"Content-Type": "application/json"},
            },
            "parameters": {},
            "returns": {
                "type": "object",
                "properties": {
                    "anomaly": {"type": "boolean", "description": "Whether an anomaly was detected"},
                    "current_rate": {"type": "number", "description": "Current error rate"},
                    "baseline_rate": {"type": "number", "description": "Baseline error rate"},
                    "incident_id": {"type": "string", "description": "Generated incident ID if anomaly found"},
                },
            },
        },
        {
            "name": "query_esql_tool",
            "description": "Execute an ES|QL query against connected Elasticsearch indices. "
                           "Use for custom anomaly analysis, error pattern investigation, or metric queries.",
            "type": "elasticsearch",
            "parameters": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "ES|QL query string. Example: FROM app-metrics | WHERE status_code == 500 | STATS count=COUNT(*) BY service",
                    },
                },
            },
            "returns": {
                "type": "object",
                "properties": {
                    "columns": {"type": "array", "description": "Column definitions"},
                    "values": {"type": "array", "description": "Result rows"},
                },
            },
            "guardrails": {
                "blocked_keywords": ["DELETE", "UPDATE", "DROP", "PUT"],
                "max_results": 10000,
            },
        },
        {
            "name": "search_runbooks_tool",
            "description": "Semantic search over the runbooks Elasticsearch index to find relevant "
                           "incident remediation procedures.",
            "type": "elasticsearch",
            "parameters": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query describing the error or incident type",
                    },
                    "max_results": {
                        "type": "integer",
                        "default": 2,
                        "description": "Maximum number of runbooks to return",
                    },
                },
            },
            "returns": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                        "tags": {"type": "array"},
                        "score": {"type": "number"},
                    },
                },
            },
        },
        {
            "name": "get_recent_commits_tool",
            "description": "Fetch recent git commits from a GitHub repository to identify "
                           "potential culprit deployments that may have caused an incident.",
            "type": "api",
            "api": {
                "method": "GET",
                "url": "https://api.github.com/repos/{repo}/commits",
                "headers": {"Accept": "application/vnd.github.v3+json"},
            },
            "parameters": {
                "type": "object",
                "required": ["repo"],
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "GitHub repository in 'owner/name' format",
                    },
                    "since_minutes": {
                        "type": "integer",
                        "default": 60,
                        "description": "How far back to look for commits (in minutes)",
                    },
                },
            },
            "returns": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "sha": {"type": "string"},
                        "message": {"type": "string"},
                        "author": {"type": "string"},
                        "date": {"type": "string"},
                    },
                },
            },
        },
        {
            "name": "post_slack_alert_tool",
            "description": "Post a formatted incident alert to a Slack channel via Incoming Webhook. "
                           "Includes severity, root cause, culprit commit, and remediation steps.",
            "type": "api",
            "api": {
                "method": "POST",
                "url": "${SLACK_WEBHOOK_URL}",
                "headers": {"Content-Type": "application/json"},
            },
            "parameters": {
                "type": "object",
                "required": ["incident_id", "severity", "root_cause"],
                "properties": {
                    "incident_id": {"type": "string"},
                    "severity": {"type": "string", "enum": ["P1", "P2", "P3"]},
                    "root_cause": {"type": "string"},
                    "recommended_steps": {"type": "array", "items": {"type": "string"}},
                    "culprit_commit": {"type": "string"},
                },
            },
            "guardrails": {
                "requires_approval": False,
                "rate_limit": "10/minute",
            },
        },
    ],
    "guardrails": {
        "approval_required_for": [
            "Any action that modifies production systems",
            "Merging pull requests",
            "Deploying code changes",
            "Rotating secrets or credentials",
            "Deleting Elasticsearch indices",
        ],
        "auto_allowed": [
            "Read-only Elasticsearch queries",
            "Slack notifications",
            "Creating draft pull requests",
            "Reading GitHub commit history",
        ],
        "p1_always_requires_approval": True,
        "rollback_requires_approval": True,
    },
    "connectors": {
        "elasticsearch": {
            "indices": ["app-metrics", "apm-errors", "runbooks", "sentinel-incidents"],
            "permissions": ["read", "search"],
        },
        "github": {
            "scopes": ["repo:read", "pulls:write"],
            "note": "Write scope only needed for draft PR creation",
        },
        "slack": {
            "type": "incoming_webhook",
            "note": "Free tier, no OAuth required",
        },
    },
}


def export_json(filepath: str = "agent_builder/sentinel_agent_config.json"):
    """Export the Agent Builder config as JSON."""
    with open(filepath, "w") as f:
        json.dump(AGENT_CONFIG, f, indent=2)
    print(f"  Exported JSON config to {filepath}")


def export_python_dict():
    """Return the config as a Python dict for programmatic use."""
    return AGENT_CONFIG


def print_kibana_import_instructions():
    """Print instructions for importing into Kibana Agent Builder UI."""
    print("""
╔══════════════════════════════════════════════════════════════════╗
║         Elastic Agent Builder — Import Instructions             ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  1. Open Kibana: http://localhost:5601                          ║
║  2. Navigate to: Management → Agent Builder                     ║
║  3. Click "Create New Agent"                                     ║
║  4. Select "Import from JSON"                                    ║
║  5. Upload: agent_builder/sentinel_agent_config.json            ║
║  6. Review the imported tools and system prompt                  ║
║  7. Connect your Elasticsearch indices:                          ║
║     - app-metrics (for anomaly detection)                       ║
║     - apm-errors (for error investigation)                      ║
║     - runbooks (for remediation search)                         ║
║     - sentinel-incidents (for incident storage)                 ║
║  8. Add your Slack webhook URL in the connector settings        ║
║  9. (Optional) Add GitHub token for commit analysis             ║
║  10. Save and activate the agent                                 ║
║                                                                  ║
║  The agent will now monitor your indices and respond to          ║
║  anomalies autonomously with human-in-the-loop approval.        ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
""")


def main():
    print("\n🤖 Sentinel-ES — Elastic Agent Builder Config Generator\n")
    export_json()
    print_kibana_import_instructions()


if __name__ == "__main__":
    main()
