"""Memory Agent — Long-term incident memory using Elasticsearch as the store."""

from __future__ import annotations

from datetime import datetime, timezone

from elasticsearch import AsyncElasticsearch

MEMORY_INDEX = "sentinel-memory"


async def ensure_memory_index(es_client: AsyncElasticsearch):
    """Create the sentinel-memory index if it doesn't exist."""
    if not await es_client.indices.exists(index=MEMORY_INDEX):
        await es_client.indices.create(
            index=MEMORY_INDEX,
            body={
                "mappings": {
                    "properties": {
                        "incident_id": {"type": "keyword"},
                        "error_signature": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                        "root_cause": {"type": "text"},
                        "resolution": {"type": "text"},
                        "resolution_time_minutes": {"type": "float"},
                        "severity": {"type": "keyword"},
                        "outcome": {"type": "keyword"},
                        "agent_findings": {"type": "object", "enabled": False},
                        "timestamp": {"type": "date"},
                        "resolved_at": {"type": "date"},
                    }
                }
            },
        )


async def remember_incident(es_client: AsyncElasticsearch, incident: dict):
    """Store an incident and its outcome in the memory index.

    Args:
        es_client: Async Elasticsearch client.
        incident: Full incident report dict.
    """
    await ensure_memory_index(es_client)

    sleuth = incident.get("agent_findings", {}).get("sleuth", {})
    error_signature = sleuth.get("primary_error", "unknown")

    doc = {
        "incident_id": incident.get("incident_id"),
        "error_signature": error_signature,
        "root_cause": incident.get("root_cause", ""),
        "resolution": incident.get("recommended_action", ""),
        "severity": incident.get("severity", "P2"),
        "outcome": incident.get("status", "open"),
        "agent_findings": incident.get("agent_findings", {}),
        "timestamp": incident.get("timestamp", datetime.now(timezone.utc).isoformat()),
        "resolved_at": incident.get("resolved_at"),
        "resolution_time_minutes": None,
    }

    if doc["resolved_at"] and doc["timestamp"]:
        try:
            ts = datetime.fromisoformat(doc["timestamp"])
            resolved = datetime.fromisoformat(doc["resolved_at"])
            doc["resolution_time_minutes"] = round((resolved - ts).total_seconds() / 60, 1)
        except (ValueError, TypeError):
            pass

    await es_client.index(
        index=MEMORY_INDEX,
        id=incident.get("incident_id"),
        document=doc,
    )


async def recall_similar(es_client: AsyncElasticsearch, error_signature: str, max_results: int = 5) -> list[dict]:
    """Search for past incidents with a similar error signature.

    Args:
        es_client: Async Elasticsearch client.
        error_signature: Error type/message to search for.
        max_results: Maximum number of past incidents to return.

    Returns:
        List of past incident memory records.
    """
    await ensure_memory_index(es_client)

    try:
        resp = await es_client.search(
            index=MEMORY_INDEX,
            body={
                "size": max_results,
                "query": {
                    "multi_match": {
                        "query": error_signature,
                        "fields": ["error_signature^3", "root_cause", "resolution"],
                        "type": "best_fields",
                        "fuzziness": "AUTO",
                    }
                },
                "sort": [{"timestamp": {"order": "desc"}}],
            },
        )

        results = []
        for hit in resp["hits"]["hits"]:
            src = hit["_source"]
            results.append({
                "incident_id": src.get("incident_id"),
                "error_signature": src.get("error_signature"),
                "root_cause": src.get("root_cause"),
                "resolution": src.get("resolution"),
                "severity": src.get("severity"),
                "outcome": src.get("outcome"),
                "resolution_time_minutes": src.get("resolution_time_minutes"),
                "timestamp": src.get("timestamp"),
                "score": hit["_score"],
            })
        return results
    except Exception:
        return []


async def get_resolution_pattern(es_client: AsyncElasticsearch, error_type: str) -> str | None:
    """Summarize how similar past incidents were resolved.

    Args:
        es_client: Async Elasticsearch client.
        error_type: The error type to look up.

    Returns:
        Human-readable summary of past resolution patterns, or None.
    """
    similar = await recall_similar(es_client, error_type, max_results=5)

    if not similar:
        return None

    resolved = [s for s in similar if s.get("outcome") in ("approved", "resolved")]
    if not resolved:
        return f"Found {len(similar)} similar past incident(s), but none were resolved yet."

    avg_time = sum(
        s.get("resolution_time_minutes", 0)
        for s in resolved
        if s.get("resolution_time_minutes")
    ) / max(len([s for s in resolved if s.get("resolution_time_minutes")]), 1)

    last = resolved[0]
    return (
        f"Last time we saw '{error_type}', the fix was: {last.get('resolution', 'N/A')} "
        f"(resolved in {last.get('resolution_time_minutes', '?')} minutes). "
        f"Seen {len(similar)} time(s) total, avg resolution: {avg_time:.0f} minutes."
    )
