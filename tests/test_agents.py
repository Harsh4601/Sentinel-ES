"""Tests for Sentinel-ES agents and tools."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("GROQ_API_KEY", "test-key-for-mocking")

pytest_plugins = ("pytest_asyncio",)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def mock_es_client():
    """Create a mock Elasticsearch async client."""
    client = AsyncMock()
    client.info = AsyncMock(return_value={"version": {"number": "8.13.0"}})
    client.indices.exists = AsyncMock(return_value=True)
    client.indices.refresh = AsyncMock()
    return client


@pytest.fixture
def mock_anomaly_data():
    """Generate mock aggregation results that simulate an anomaly."""
    now = datetime.now(timezone.utc)
    return {
        "recent": {
            "aggregations": {"avg_count": {"value": 75.0}},
        },
        "baseline": {
            "aggregations": {"avg_count": {"value": 2.5}},
        },
        "spike_start": {
            "aggregations": {"first_spike": {"value_as_string": (now - timedelta(minutes=30)).isoformat()}},
        },
    }


@pytest.fixture
def mock_normal_data():
    """Generate mock aggregation results with no anomaly."""
    return {
        "recent": {
            "aggregations": {"avg_count": {"value": 3.0}},
        },
        "baseline": {
            "aggregations": {"avg_count": {"value": 2.5}},
        },
    }


@pytest.fixture
def mock_apm_errors():
    """Generate mock APM error search results."""
    return {
        "aggregations": {
            "top_errors": {
                "buckets": [
                    {
                        "key": "ConnectionRefusedError",
                        "doc_count": 15,
                        "first_seen": {"value_as_string": "2026-02-25T10:00:00Z"},
                        "last_seen": {"value_as_string": "2026-02-25T10:25:00Z"},
                        "sample": {
                            "hits": {
                                "hits": [
                                    {
                                        "_source": {
                                            "message": "Connection refused to database at 10.0.3.14:5432",
                                            "stack_trace": "Traceback...\nConnectionRefusedError",
                                            "service": "payment-service",
                                        }
                                    }
                                ]
                            }
                        },
                    },
                    {
                        "key": "TimeoutError",
                        "doc_count": 8,
                        "first_seen": {"value_as_string": "2026-02-25T10:05:00Z"},
                        "last_seen": {"value_as_string": "2026-02-25T10:28:00Z"},
                        "sample": {
                            "hits": {
                                "hits": [
                                    {
                                        "_source": {
                                            "message": "Read timed out",
                                            "stack_trace": "Traceback...\nTimeoutError",
                                            "service": "auth-service",
                                        }
                                    }
                                ]
                            }
                        },
                    },
                ]
            }
        }
    }


@pytest.fixture
def mock_runbook_results():
    """Generate mock runbook search results."""
    return {
        "hits": {
            "hits": [
                {
                    "_score": 5.2,
                    "_source": {
                        "title": "Runbook: Database Connection Timeout",
                        "content": "## Symptoms\n- ConnectionRefusedError\n## Remediation\n1. Restart pods\n2. Increase pool size",
                        "tags": ["database", "timeout"],
                    },
                },
                {
                    "_score": 3.1,
                    "_source": {
                        "title": "Runbook: HTTP 5xx Error Spike",
                        "content": "## Symptoms\n- 500 error spike\n## Remediation\n1. Check logs\n2. Rollback",
                        "tags": ["http", "500"],
                    },
                },
            ]
        }
    }


# ── Test 1: Anomaly Detection ────────────────────────────────────────


@pytest.mark.asyncio
async def test_anomaly_detection(mock_es_client, mock_anomaly_data):
    """Seeds ES with a spike, verifies detect_anomalies() returns anomaly=True."""
    from tools.esql_tool import detect_anomalies

    async def mock_search(**kwargs):
        body = kwargs.get("body", {})
        aggs = body.get("aggs", {})
        query_filters = body.get("query", {}).get("bool", {}).get("filter", [])

        if "first_spike" in aggs:
            return mock_anomaly_data["spike_start"]

        has_lt = any(
            "lt" in f.get("range", {}).get("timestamp", {})
            for f in query_filters
            if "range" in f and "timestamp" in f.get("range", {})
        )
        if has_lt:
            return mock_anomaly_data["baseline"]
        return mock_anomaly_data["recent"]

    mock_es_client.search = AsyncMock(side_effect=mock_search)
    mock_es_client.esql = MagicMock()
    mock_es_client.esql.query = AsyncMock(return_value={"error": "ES|QL not available in mock"})

    result = await detect_anomalies(mock_es_client)

    assert result["anomaly"] is True
    assert result["current_rate"] > result["baseline_rate"] * 3
    assert result["spike_started_at"] is not None


# ── Test 2: Sleuth Agent ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sleuth_agent(mock_es_client, mock_apm_errors):
    """Mocks ES response, verifies SleuthAgent returns required keys."""
    mock_es_client.search = AsyncMock(return_value=mock_apm_errors)

    mock_llm_response = '{"primary_error": "ConnectionRefusedError", "affected_service": "payment-service", "likely_cause": "Database connection pool exhausted", "confidence": "high"}'

    with patch("agents.base_agent.AsyncGroq"):
        from agents.sleuth_agent import SleuthAgent

        agent = SleuthAgent()
        agent._client = AsyncMock()

        async def mock_run(user_message, context=None):
            return mock_llm_response

        agent.run = mock_run
        result = await agent.investigate(mock_es_client, since_minutes=30)

    assert "primary_error" in result
    assert "affected_service" in result
    assert "likely_cause" in result
    assert "confidence" in result
    assert "raw_errors" in result
    assert result["confidence"] in ("high", "medium", "low")


# ── Test 3: Historian Agent ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_historian_agent():
    """Uses mock commit data, verifies it returns culprit_commit_sha."""
    mock_llm_response = '{"culprit_commit_sha": "a1b2c3d4", "culprit_commit_message": "fix: update database connection pool settings", "author": "Alice Chen", "pr_number": "142", "reasoning": "Pool size reduction matches the connection errors", "confidence": "high"}'

    with patch("agents.base_agent.AsyncGroq"):
        from agents.historian_agent import HistorianAgent

        agent = HistorianAgent()
        agent._client = AsyncMock()

        async def mock_run(user_message, context=None):
            return mock_llm_response

        agent.run = mock_run
        result = await agent.find_culprit_commit(
            "ConnectionRefusedError: database unreachable",
            repo="",
            since_minutes=60,
        )

    assert "culprit_commit_sha" in result
    assert result["culprit_commit_sha"] is not None
    assert "author" in result
    assert "reasoning" in result
    assert "confidence" in result


# ── Test 4: Scribe Agent ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scribe_agent(mock_es_client, mock_runbook_results):
    """Mocks ES runbook search, verifies 3 remediation steps returned."""
    mock_es_client.search = AsyncMock(return_value=mock_runbook_results)

    mock_llm_response = '{"matched_runbooks": ["Runbook: Database Connection Timeout"], "recommended_steps": ["Restart application pods", "Increase connection pool size to 25", "Failover to read replica"], "rollback_possible": true, "estimated_fix_time": "10 minutes"}'

    with patch("agents.base_agent.AsyncGroq"):
        from agents.scribe_agent import ScribeAgent

        agent = ScribeAgent()
        agent._client = AsyncMock()

        async def mock_run(user_message, context=None):
            return mock_llm_response

        agent.run = mock_run
        result = await agent.find_runbook(mock_es_client, "ConnectionRefusedError")

    assert "matched_runbooks" in result
    assert "recommended_steps" in result
    assert len(result["recommended_steps"]) == 3
    assert "rollback_possible" in result
    assert "estimated_fix_time" in result


# ── Test 5: Orchestrator Full Pipeline ───────────────────────────────


@pytest.mark.asyncio
async def test_orchestrator_full(mock_es_client, mock_anomaly_data, mock_apm_errors, mock_runbook_results):
    """End-to-end test with all mocks, verifies final incident report structure."""

    async def mock_search(**kwargs):
        body = kwargs.get("body", {})
        aggs = body.get("aggs", {})
        index = kwargs.get("index", "")

        if index == "runbooks":
            return mock_runbook_results
        if "top_errors" in aggs:
            return mock_apm_errors
        if "first_spike" in aggs:
            return mock_anomaly_data["spike_start"]

        query_filters = body.get("query", {}).get("bool", {}).get("filter", [])
        has_lt = any(
            "lt" in f.get("range", {}).get("timestamp", {})
            for f in query_filters
            if "range" in f and "timestamp" in f.get("range", {})
        )
        if has_lt:
            return mock_anomaly_data["baseline"]
        return mock_anomaly_data["recent"]

    mock_es_client.search = AsyncMock(side_effect=mock_search)
    mock_es_client.esql = MagicMock()
    mock_es_client.esql.query = AsyncMock(return_value={"error": "mock"})

    synthesis_response = '{"root_cause": "Database connection pool exhausted after pool size reduction", "severity": "P1", "recommended_action": "Rollback commit a1b2c3d4 and increase pool size", "conflicts_resolved": "All agents agree on database connection as root cause", "slack_summary": "P1 incident detected: database connection failures caused by pool size reduction."}'
    sleuth_response = '{"primary_error": "ConnectionRefusedError", "affected_service": "payment-service", "likely_cause": "Database pool exhausted", "confidence": "high"}'
    historian_response = '{"culprit_commit_sha": "a1b2c3d4", "culprit_commit_message": "fix: update pool", "author": "Alice", "pr_number": "142", "reasoning": "Pool reduction", "confidence": "high"}'
    scribe_response = '{"matched_runbooks": ["DB Timeout Runbook"], "recommended_steps": ["Restart pods", "Increase pool", "Failover"], "rollback_possible": true, "estimated_fix_time": "10 minutes"}'

    with patch("agents.base_agent.AsyncGroq"):
        from agents.orchestrator import OrchestratorAgent

        orchestrator = OrchestratorAgent()

        async def make_mock_run(response):
            async def mock_run(user_message, context=None):
                return response
            return mock_run

        orchestrator.run = await make_mock_run(synthesis_response)
        orchestrator.sleuth.run = await make_mock_run(sleuth_response)
        orchestrator.historian.run = await make_mock_run(historian_response)
        orchestrator.scribe.run = await make_mock_run(scribe_response)

        report = await orchestrator.run_investigation(mock_es_client, "example/repo")

    assert report.get("status") != "no_anomaly"
    assert "incident_id" in report
    assert "severity" in report
    assert report["severity"] in ("P1", "P2", "P3")
    assert "root_cause" in report
    assert "culprit_commit" in report
    assert "agent_findings" in report
    assert "sleuth" in report["agent_findings"]
    assert "historian" in report["agent_findings"]
    assert "scribe" in report["agent_findings"]
    assert "slack_message" in report
    assert "timestamp" in report


# ── Test 6: No Anomaly ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_anomaly(mock_es_client, mock_normal_data):
    """Seeds ES with normal data, verifies orchestrator returns no_anomaly status."""

    async def mock_search(**kwargs):
        body = kwargs.get("body", {})
        query_filters = body.get("query", {}).get("bool", {}).get("filter", [])
        has_lt = any(
            "lt" in f.get("range", {}).get("timestamp", {})
            for f in query_filters
            if "range" in f and "timestamp" in f.get("range", {})
        )
        if has_lt:
            return mock_normal_data["baseline"]
        return mock_normal_data["recent"]

    mock_es_client.search = AsyncMock(side_effect=mock_search)
    mock_es_client.esql = MagicMock()
    mock_es_client.esql.query = AsyncMock(return_value={"error": "mock"})

    with patch("agents.base_agent.AsyncGroq"):
        from agents.orchestrator import OrchestratorAgent

        orchestrator = OrchestratorAgent()
        report = await orchestrator.run_investigation(mock_es_client)

    assert report["status"] == "no_anomaly"
