"""Scribe Agent — Runbook researcher that finds remediation steps."""

from __future__ import annotations

import json

from elasticsearch import AsyncElasticsearch

from agents.base_agent import BaseAgent

SCRIBE_SYSTEM_PROMPT = """You are a knowledge base expert. You find relevant runbooks and distill them into \
actionable remediation steps for the current incident.

Always respond with valid JSON in this exact format:
{
  "matched_runbooks": ["<runbook title 1>", "<runbook title 2>"],
  "recommended_steps": ["<step 1>", "<step 2>", "<step 3>"],
  "rollback_possible": true|false,
  "estimated_fix_time": "<e.g. 5 minutes, 30 minutes>"
}"""


class ScribeAgent(BaseAgent):
    """Searches runbooks and distills actionable remediation steps."""

    def __init__(self):
        super().__init__(
            name="Scribe",
            system_prompt=SCRIBE_SYSTEM_PROMPT,
        )

    async def find_runbook(
        self, es_client: AsyncElasticsearch, error_summary: str
    ) -> dict:
        """Search runbooks for the given error and produce remediation steps.

        Args:
            es_client: Async Elasticsearch client.
            error_summary: Description of the error to search runbooks for.

        Returns:
            Dict with matched_runbooks, recommended_steps, rollback_possible, estimated_fix_time.
        """
        self.log_activity("find_runbook:start", f"Searching runbooks for: {error_summary[:80]}")

        runbooks = await self._search_runbooks(es_client, error_summary)

        if not runbooks:
            self.log_activity("find_runbook:no_match", "No matching runbooks found")
            return {
                "matched_runbooks": [],
                "recommended_steps": [
                    "Check application logs for detailed error messages",
                    "Review recent deployments and configuration changes",
                    "Escalate to the on-call engineer if issue persists",
                ],
                "rollback_possible": False,
                "estimated_fix_time": "unknown",
            }

        runbook_text = "\n\n---\n\n".join(
            f"Title: {rb['title']}\n{rb['content']}" for rb in runbooks[:2]
        )

        prompt = (
            f"Error summary: {error_summary}\n\n"
            f"Matching runbooks:\n{runbook_text}\n\n"
            "Based on these runbooks and the error, what are the top 3 remediation steps? "
            "Is rollback possible? Estimate fix time. Respond with JSON only."
        )

        response_text = await self.run(prompt)
        findings = self._parse_findings(response_text, runbooks)

        self.memory["last_runbook_search"] = findings
        self.log_activity("find_runbook:complete", f"Found {len(findings['matched_runbooks'])} runbooks")

        return findings

    async def _search_runbooks(
        self, es_client: AsyncElasticsearch, error_summary: str
    ) -> list[dict]:
        """Search the runbooks index using keyword matching."""
        try:
            resp = await es_client.search(
                index="runbooks",
                body={
                    "size": 2,
                    "query": {
                        "multi_match": {
                            "query": error_summary,
                            "fields": ["title^2", "content", "tags^3"],
                            "type": "best_fields",
                            "fuzziness": "AUTO",
                        }
                    },
                },
            )

            results = []
            for hit in resp["hits"]["hits"]:
                src = hit["_source"]
                results.append({
                    "title": src.get("title", "Untitled"),
                    "content": src.get("content", ""),
                    "tags": src.get("tags", []),
                    "score": hit["_score"],
                })
            return results

        except Exception as e:
            self.log_activity("search_runbooks:error", str(e))
            return []

    def _parse_findings(self, response_text: str, runbooks: list[dict]) -> dict:
        """Parse LLM response into structured runbook findings."""
        try:
            cleaned = response_text.strip()
            if "```json" in cleaned:
                cleaned = cleaned.split("```json")[1].split("```")[0].strip()
            elif "```" in cleaned:
                cleaned = cleaned.split("```")[1].split("```")[0].strip()
            data = json.loads(cleaned)

            steps = data.get("recommended_steps", [])
            if len(steps) > 3:
                steps = steps[:3]
            elif len(steps) < 3:
                defaults = [
                    "Check application logs for detailed error messages",
                    "Review recent deployments and configuration changes",
                    "Escalate to the on-call engineer if issue persists",
                ]
                while len(steps) < 3:
                    steps.append(defaults[len(steps)])

            return {
                "matched_runbooks": data.get("matched_runbooks", [rb["title"] for rb in runbooks[:2]]),
                "recommended_steps": steps,
                "rollback_possible": data.get("rollback_possible", False),
                "estimated_fix_time": data.get("estimated_fix_time", "15 minutes"),
            }
        except (json.JSONDecodeError, IndexError):
            return {
                "matched_runbooks": [rb["title"] for rb in runbooks[:2]],
                "recommended_steps": [
                    "Follow the matched runbook procedures",
                    "Review recent changes and consider rollback",
                    "Escalate if not resolved within 15 minutes",
                ],
                "rollback_possible": True,
                "estimated_fix_time": "15 minutes",
            }
