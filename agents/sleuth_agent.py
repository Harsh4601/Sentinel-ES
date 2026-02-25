"""Sleuth Agent — APM error investigator that identifies root error patterns."""

from __future__ import annotations

import json

from elasticsearch import AsyncElasticsearch

from agents.base_agent import BaseAgent
from tools.esql_tool import search_apm_errors

SLEUTH_SYSTEM_PROMPT = """You are a senior SRE investigating application errors. You receive raw error data from \
Elasticsearch APM and identify the root error pattern, affected service, and likely cause.
Be concise. Output structured findings.

Always respond with valid JSON in this exact format:
{
  "primary_error": "<the main error type/message>",
  "affected_service": "<service name>",
  "likely_cause": "<brief root cause analysis>",
  "confidence": "high|medium|low"
}"""


class SleuthAgent(BaseAgent):
    """Investigates APM errors to identify root cause patterns."""

    def __init__(self):
        super().__init__(
            name="Sleuth",
            system_prompt=SLEUTH_SYSTEM_PROMPT,
        )

    async def investigate(
        self, es_client: AsyncElasticsearch, since_minutes: int = 30
    ) -> dict:
        """Pull recent APM errors and use LLM to identify root cause pattern.

        Args:
            es_client: Async Elasticsearch client.
            since_minutes: How far back to look for errors.

        Returns:
            Dict with primary_error, affected_service, likely_cause, raw_errors, confidence.
        """
        self.log_activity("investigate:start", f"Searching errors from last {since_minutes} minutes")

        errors = await search_apm_errors(es_client, since_minutes)
        if not errors or (len(errors) == 1 and "error" in errors[0]):
            self.log_activity("investigate:no_data", "No errors found or ES error")
            return {
                "primary_error": "No errors found",
                "affected_service": "unknown",
                "likely_cause": "No recent errors detected in the time window",
                "raw_errors": errors,
                "confidence": "low",
            }

        errors_summary = "\n".join(
            f"- [{e.get('error_type', 'unknown')}] {e.get('message', '')} "
            f"(count: {e.get('count', 0)}, service: {e.get('service', '?')}, "
            f"last_seen: {e.get('last_seen', '?')})"
            for e in errors
        )

        prompt = (
            f"Here are the top errors from the last {since_minutes} minutes:\n\n"
            f"{errors_summary}\n\n"
            "What is the primary error pattern? What service is affected? "
            "What likely caused this? Respond with JSON only."
        )

        response_text = await self.run(prompt)

        findings = self._parse_findings(response_text)
        findings["raw_errors"] = errors

        self.memory["last_investigation"] = findings
        self.log_activity("investigate:complete", f"Found: {findings['primary_error']}")

        return findings

    def _parse_findings(self, response_text: str) -> dict:
        """Parse the LLM response into structured findings."""
        try:
            cleaned = response_text.strip()
            if "```json" in cleaned:
                cleaned = cleaned.split("```json")[1].split("```")[0].strip()
            elif "```" in cleaned:
                cleaned = cleaned.split("```")[1].split("```")[0].strip()
            data = json.loads(cleaned)
            return {
                "primary_error": data.get("primary_error", "Unknown"),
                "affected_service": data.get("affected_service", "unknown"),
                "likely_cause": data.get("likely_cause", "Unable to determine"),
                "confidence": data.get("confidence", "medium"),
            }
        except (json.JSONDecodeError, IndexError):
            return {
                "primary_error": response_text[:200],
                "affected_service": "unknown",
                "likely_cause": response_text,
                "confidence": "low",
            }
