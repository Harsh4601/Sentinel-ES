"""ES|QL query tools for anomaly detection and APM error analysis."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from elasticsearch import AsyncElasticsearch


async def run_esql(es_client: AsyncElasticsearch, query: str) -> dict:
    """Execute an arbitrary ES|QL query and return parsed results.

    Args:
        es_client: Async Elasticsearch client instance.
        query: Raw ES|QL query string.

    Returns:
        Dictionary with 'columns' and 'values' keys from the ES|QL response.
    """
    try:
        response = await es_client.esql.query(query=query, format="json")
        return response
    except Exception as e:
        return {"error": str(e), "columns": [], "values": []}


async def detect_anomalies(es_client: AsyncElasticsearch) -> dict:
    """Detect HTTP 500 anomalies by comparing recent rate vs baseline.

    Compares the average HTTP 500 count in the last 30 minutes against the
    previous 2-hour baseline. Flags anomaly when current rate > 3x baseline.

    Returns:
        Dict with anomaly status, current_rate, baseline_rate, and spike_started_at.
    """
    now = datetime.now(timezone.utc)
    thirty_min_ago = (now - timedelta(minutes=30)).isoformat()
    two_hours_ago = (now - timedelta(hours=2)).isoformat()

    try:
        recent_query = f"""
            FROM app-metrics
            | WHERE status_code == 500
              AND timestamp >= \"{thirty_min_ago}\"
            | STATS avg_count = AVG(count)
        """
        recent = await run_esql(es_client, recent_query)

        baseline_query = f"""
            FROM app-metrics
            | WHERE status_code == 500
              AND timestamp >= \"{two_hours_ago}\"
              AND timestamp < \"{thirty_min_ago}\"
            | STATS avg_count = AVG(count)
        """
        baseline = await run_esql(es_client, baseline_query)

        if "error" in recent or "error" in baseline:
            return await _detect_anomalies_fallback(es_client, now, thirty_min_ago, two_hours_ago)

        current_rate = _extract_value(recent, "avg_count", 0.0)
        baseline_rate = _extract_value(baseline, "avg_count", 0.0)

        is_anomaly = baseline_rate > 0 and current_rate > 3 * baseline_rate

        spike_started_at = None
        if is_anomaly:
            spike_started_at = await _find_spike_start(es_client, two_hours_ago, now)

        return {
            "anomaly": is_anomaly,
            "current_rate": round(current_rate, 2),
            "baseline_rate": round(baseline_rate, 2),
            "spike_started_at": spike_started_at,
        }

    except Exception as e:
        return await _detect_anomalies_fallback(es_client, now, thirty_min_ago, two_hours_ago)


async def _detect_anomalies_fallback(
    es_client: AsyncElasticsearch, now, thirty_min_ago, two_hours_ago
) -> dict:
    """Fallback anomaly detection using standard ES queries when ES|QL is unavailable."""
    try:
        recent_resp = await es_client.search(
            index="app-metrics",
            body={
                "size": 0,
                "query": {
                    "bool": {
                        "filter": [
                            {"term": {"status_code": 500}},
                            {"range": {"timestamp": {"gte": thirty_min_ago if isinstance(thirty_min_ago, str) else thirty_min_ago.isoformat()}}},
                        ]
                    }
                },
                "aggs": {"avg_count": {"avg": {"field": "count"}}},
            },
        )

        baseline_resp = await es_client.search(
            index="app-metrics",
            body={
                "size": 0,
                "query": {
                    "bool": {
                        "filter": [
                            {"term": {"status_code": 500}},
                            {"range": {"timestamp": {
                                "gte": two_hours_ago if isinstance(two_hours_ago, str) else two_hours_ago.isoformat(),
                                "lt": thirty_min_ago if isinstance(thirty_min_ago, str) else thirty_min_ago.isoformat(),
                            }}},
                        ]
                    }
                },
                "aggs": {"avg_count": {"avg": {"field": "count"}}},
            },
        )

        current_rate = recent_resp["aggregations"]["avg_count"]["value"] or 0.0
        baseline_rate = baseline_resp["aggregations"]["avg_count"]["value"] or 0.0
        is_anomaly = baseline_rate > 0 and current_rate > 3 * baseline_rate

        return {
            "anomaly": is_anomaly,
            "current_rate": round(current_rate, 2),
            "baseline_rate": round(baseline_rate, 2),
            "spike_started_at": (now - timedelta(minutes=30)).isoformat() if is_anomaly else None,
        }
    except Exception as e:
        return {
            "anomaly": False,
            "current_rate": 0.0,
            "baseline_rate": 0.0,
            "spike_started_at": None,
            "error": str(e),
        }


async def _find_spike_start(es_client: AsyncElasticsearch, since: str, until: datetime) -> str | None:
    """Find the approximate timestamp when the error spike started."""
    try:
        resp = await es_client.search(
            index="app-metrics",
            body={
                "size": 0,
                "query": {
                    "bool": {
                        "filter": [
                            {"term": {"status_code": 500}},
                            {"range": {"count": {"gte": 20}}},
                            {"range": {"timestamp": {"gte": since}}},
                        ]
                    }
                },
                "aggs": {
                    "first_spike": {"min": {"field": "timestamp"}}
                },
            },
        )
        val = resp["aggregations"]["first_spike"]["value_as_string"]
        return val
    except Exception:
        return None


async def search_apm_errors(
    es_client: AsyncElasticsearch, since_minutes: int = 30
) -> list[dict]:
    """Return the top 5 most frequent error messages from apm-errors index.

    Args:
        es_client: Async Elasticsearch client instance.
        since_minutes: Look back this many minutes from now.

    Returns:
        List of dicts, each containing: message, count, first_seen, last_seen, sample_stack_trace.
    """
    since = (datetime.now(timezone.utc) - timedelta(minutes=since_minutes)).isoformat()

    try:
        resp = await es_client.search(
            index="apm-errors",
            body={
                "size": 0,
                "query": {
                    "range": {"timestamp": {"gte": since}}
                },
                "aggs": {
                    "top_errors": {
                        "terms": {"field": "error_type", "size": 5},
                        "aggs": {
                            "first_seen": {"min": {"field": "timestamp"}},
                            "last_seen": {"max": {"field": "timestamp"}},
                            "sample": {
                                "top_hits": {
                                    "size": 1,
                                    "_source": ["message", "stack_trace", "service"],
                                }
                            },
                        },
                    }
                },
            },
        )

        results = []
        for bucket in resp["aggregations"]["top_errors"]["buckets"]:
            hit = bucket["sample"]["hits"]["hits"][0]["_source"]
            results.append({
                "error_type": bucket["key"],
                "message": hit.get("message", ""),
                "count": bucket["doc_count"],
                "first_seen": bucket["first_seen"]["value_as_string"],
                "last_seen": bucket["last_seen"]["value_as_string"],
                "sample_stack_trace": hit.get("stack_trace", ""),
                "service": hit.get("service", "unknown"),
            })

        return results

    except Exception as e:
        return [{"error": str(e)}]


def _extract_value(esql_response: dict, column_name: str, default=0.0):
    """Extract a single value from an ES|QL response by column name."""
    if "error" in esql_response:
        return default
    columns = esql_response.get("columns", [])
    values = esql_response.get("values", [])
    if not columns or not values:
        return default
    for i, col in enumerate(columns):
        if col.get("name") == column_name:
            return values[0][i] if values[0][i] is not None else default
    return default
