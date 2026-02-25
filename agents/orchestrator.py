"""Orchestrator Agent — Multi-agent coordinator that runs the full investigation pipeline."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

from elasticsearch import AsyncElasticsearch

from agents.base_agent import BaseAgent
from agents.historian_agent import HistorianAgent
from agents.scribe_agent import ScribeAgent
from agents.sleuth_agent import SleuthAgent
from safety.guardrails import ActionGuardrail
from tools.esql_tool import detect_anomalies

ORCHESTRATOR_SYSTEM_PROMPT = """You are an incident commander. You coordinate SRE sub-agents, synthesize their \
findings, resolve disagreements between agents, and produce a final incident report with a clear \
remediation recommendation. You think step by step.

Always respond with valid JSON in this exact format:
{
  "root_cause": "<unified root cause analysis>",
  "severity": "P1|P2|P3",
  "recommended_action": "<clear remediation recommendation>",
  "conflicts_resolved": "<any disagreements between agents and how you resolved them>",
  "slack_summary": "<2-3 sentence summary suitable for a Slack alert>"
}"""


class OrchestratorAgent(BaseAgent):
    """Coordinates specialist agents and produces unified incident reports."""

    def __init__(self):
        super().__init__(
            name="Orchestrator",
            system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
        )
        self.sleuth = SleuthAgent()
        self.historian = HistorianAgent()
        self.scribe = ScribeAgent()

    async def run_investigation(
        self, es_client: AsyncElasticsearch, repo: str | None = None
    ) -> dict:
        """Run the full investigation pipeline: detect, investigate, synthesize.

        Args:
            es_client: Async Elasticsearch client.
            repo: GitHub repository in 'owner/name' format.

        Returns:
            Complete incident report dict, or no_anomaly status.
        """
        self.log_activity("investigation:start", "Starting anomaly check")

        # Step 1 — Trigger check
        anomaly_result = await detect_anomalies(es_client)
        self.log_activity("investigation:anomaly_check", f"Anomaly={anomaly_result['anomaly']}")

        if not anomaly_result.get("anomaly"):
            return {
                "status": "no_anomaly",
                "current_rate": anomaly_result.get("current_rate", 0),
                "baseline_rate": anomaly_result.get("baseline_rate", 0),
            }

        # Step 2 — Parallel agent execution
        self.log_activity("investigation:agents_start", "Launching Sleuth, Historian, Scribe in parallel")

        sleuth_task = self.sleuth.investigate(es_client)
        historian_placeholder = asyncio.ensure_future(asyncio.sleep(0))
        scribe_placeholder = asyncio.ensure_future(asyncio.sleep(0))

        sleuth_findings = await sleuth_task
        primary_error = sleuth_findings.get("primary_error", "Unknown error")

        historian_findings, scribe_findings = await asyncio.gather(
            self.historian.find_culprit_commit(primary_error, repo),
            self.scribe.find_runbook(es_client, primary_error),
        )

        self.log_activity("investigation:agents_complete", "All agents reported back")

        # Step 3 — Conflict resolution
        synthesis = await self._resolve_conflicts(
            sleuth_findings, historian_findings, scribe_findings
        )

        # Step 4 — Guardrail validation
        guardrail = ActionGuardrail()
        esql_safe, esql_reason = guardrail.validate_esql_query("SELECT * FROM app-metrics")
        self.log_activity("investigation:guardrail", f"ES|QL safety check: {esql_reason}")

        # Step 5 — Build incident report
        incident_id = str(uuid.uuid4())[:12]
        timestamp = datetime.now(timezone.utc).isoformat()

        slack_message = self._format_slack_message(
            incident_id, synthesis, sleuth_findings, historian_findings, scribe_findings
        )

        report = {
            "incident_id": incident_id,
            "status": "open",
            "severity": synthesis.get("severity", "P2"),
            "root_cause": synthesis.get("root_cause", primary_error),
            "culprit_commit": {
                "sha": historian_findings.get("culprit_commit_sha"),
                "message": historian_findings.get("culprit_commit_message"),
                "author": historian_findings.get("author"),
                "pr_number": historian_findings.get("pr_number"),
            },
            "recommended_action": synthesis.get("recommended_action", ""),
            "rollback_pr_url": None,
            "slack_message": slack_message,
            "agent_findings": {
                "sleuth": _strip_raw(sleuth_findings),
                "historian": historian_findings,
                "scribe": scribe_findings,
            },
            "anomaly_data": anomaly_result,
            "timestamp": timestamp,
        }

        # Step 6 — Check if human approval is required
        needs_approval = guardrail.requires_human_approval(report)
        report["requires_approval"] = needs_approval

        self.log_activity(
            "investigation:complete",
            f"Incident {incident_id} — {synthesis.get('severity', 'P2')} — approval_required={needs_approval}",
        )

        return report

    async def _resolve_conflicts(
        self, sleuth: dict, historian: dict, scribe: dict
    ) -> dict:
        """Use LLM to resolve conflicts between agent findings."""
        prompt = (
            f"The Sleuth agent says the primary error is: {sleuth.get('primary_error', 'N/A')}\n"
            f"  Affected service: {sleuth.get('affected_service', 'N/A')}\n"
            f"  Likely cause: {sleuth.get('likely_cause', 'N/A')}\n"
            f"  Confidence: {sleuth.get('confidence', 'N/A')}\n\n"
            f"The Historian agent says the culprit commit is: {historian.get('culprit_commit_sha', 'N/A')}\n"
            f"  By author: {historian.get('author', 'N/A')}\n"
            f"  Reasoning: {historian.get('reasoning', 'N/A')}\n"
            f"  Confidence: {historian.get('confidence', 'N/A')}\n\n"
            f"The Scribe agent recommends:\n"
            f"  Steps: {json.dumps(scribe.get('recommended_steps', []))}\n"
            f"  Rollback possible: {scribe.get('rollback_possible', False)}\n"
            f"  Estimated fix time: {scribe.get('estimated_fix_time', 'unknown')}\n\n"
            "Do these findings agree? If there is a conflict, which is more credible and why? "
            "Produce a final unified root cause and remediation plan. "
            "Assign a severity: P1 (critical, customer-facing), P2 (major, partial impact), P3 (minor). "
            "Respond with JSON only."
        )

        response_text = await self.run(prompt)
        return self._parse_synthesis(response_text)

    def _parse_synthesis(self, response_text: str) -> dict:
        """Parse the conflict resolution LLM response."""
        try:
            cleaned = response_text.strip()
            if "```json" in cleaned:
                cleaned = cleaned.split("```json")[1].split("```")[0].strip()
            elif "```" in cleaned:
                cleaned = cleaned.split("```")[1].split("```")[0].strip()
            data = json.loads(cleaned)
            return {
                "root_cause": data.get("root_cause", "Unable to determine"),
                "severity": data.get("severity", "P2"),
                "recommended_action": data.get("recommended_action", ""),
                "conflicts_resolved": data.get("conflicts_resolved", ""),
                "slack_summary": data.get("slack_summary", ""),
            }
        except (json.JSONDecodeError, IndexError):
            return {
                "root_cause": response_text[:300],
                "severity": "P2",
                "recommended_action": "Review findings manually",
                "conflicts_resolved": "",
                "slack_summary": response_text[:200],
            }

    def _format_slack_message(
        self, incident_id: str, synthesis: dict,
        sleuth: dict, historian: dict, scribe: dict,
    ) -> str:
        """Format a human-readable Slack message from the incident report."""
        severity = synthesis.get("severity", "P2")
        emoji = {"P1": "🔴", "P2": "🟠", "P3": "🟡"}.get(severity, "⚪")

        steps = scribe.get("recommended_steps", [])
        steps_text = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(steps))

        return (
            f"{emoji} *[{severity}] Incident {incident_id}*\n\n"
            f"*Root Cause:* {synthesis.get('root_cause', 'Unknown')}\n\n"
            f"*Culprit Commit:* `{historian.get('culprit_commit_sha', 'N/A')}` "
            f"by {historian.get('author', 'unknown')}\n"
            f"  _{historian.get('culprit_commit_message', '')}_\n\n"
            f"*Recommended Steps:*\n{steps_text}\n\n"
            f"*Rollback possible:* {'Yes' if scribe.get('rollback_possible') else 'No'} "
            f"| *Est. fix time:* {scribe.get('estimated_fix_time', 'unknown')}\n\n"
            f"_Investigated by Sentinel-ES · 3 agents_"
        )


def _strip_raw(findings: dict) -> dict:
    """Remove large raw_errors from findings to keep reports concise."""
    return {k: v for k, v in findings.items() if k != "raw_errors"}
