"""FastAPI application for Sentinel-ES webhook and management endpoints."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv
from elasticsearch import AsyncElasticsearch
from fastapi import BackgroundTasks, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from agents.base_agent import AGENT_ACTIVITY_LOG
from agents.orchestrator import OrchestratorAgent
from tools.slack_tool import post_incident_alert, post_resolution

load_dotenv()

ES_HOST = os.getenv("ES_HOST", "http://localhost:9200")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")
POLL_INTERVAL = int(os.getenv("ANOMALY_POLL_INTERVAL_SECONDS", "60"))
INCIDENTS_INDEX = os.getenv("ES_INDEX_INCIDENTS", "sentinel-incidents")

es_client: AsyncElasticsearch | None = None
poll_task: asyncio.Task | None = None
last_anomaly_check: str | None = None


async def _ensure_incidents_index():
    """Create the sentinel-incidents index if it doesn't exist."""
    if not await es_client.indices.exists(index=INCIDENTS_INDEX):
        await es_client.indices.create(
            index=INCIDENTS_INDEX,
            body={
                "mappings": {
                    "properties": {
                        "incident_id": {"type": "keyword"},
                        "status": {"type": "keyword"},
                        "severity": {"type": "keyword"},
                        "root_cause": {"type": "text"},
                        "recommended_action": {"type": "text"},
                        "culprit_commit": {"type": "object"},
                        "agent_findings": {"type": "object", "enabled": False},
                        "anomaly_data": {"type": "object"},
                        "slack_message": {"type": "text"},
                        "timestamp": {"type": "date"},
                        "resolved_at": {"type": "date"},
                        "resolved_by": {"type": "keyword"},
                    }
                }
            },
        )


async def _anomaly_poll_loop():
    """Background loop that checks for anomalies on a schedule."""
    global last_anomaly_check
    while True:
        try:
            last_anomaly_check = datetime.now(timezone.utc).isoformat()
            orchestrator = OrchestratorAgent()
            report = await orchestrator.run_investigation(es_client, GITHUB_REPO)

            if report.get("status") != "no_anomaly":
                await _store_incident(report)
                await post_incident_alert(report)
        except Exception as e:
            print(f"[Poll] Error during anomaly check: {e}")

        await asyncio.sleep(POLL_INTERVAL)


async def _store_incident(report: dict):
    """Store an incident report in Elasticsearch."""
    try:
        await es_client.index(
            index=INCIDENTS_INDEX,
            id=report["incident_id"],
            document=report,
        )
        await es_client.indices.refresh(index=INCIDENTS_INDEX)
    except Exception as e:
        print(f"[Store] Error saving incident: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize ES client on startup, cleanup on shutdown."""
    global es_client, poll_task
    es_client = AsyncElasticsearch([ES_HOST])

    try:
        info = await es_client.info()
        print(f"[Startup] Connected to Elasticsearch {info['version']['number']}")
        await _ensure_incidents_index()
        poll_task = asyncio.create_task(_anomaly_poll_loop())
    except Exception as e:
        print(f"[Startup] Elasticsearch not available: {e}")

    yield

    if poll_task:
        poll_task.cancel()
    if es_client:
        await es_client.close()


app = FastAPI(
    title="Sentinel-ES",
    description="Autonomous SRE powered by Elasticsearch Agent Builder",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/webhook/alert")
async def webhook_alert(background_tasks: BackgroundTasks):
    """Trigger an anomaly investigation.

    Can be called by an external alerting system or manually to trigger detection.
    """
    orchestrator = OrchestratorAgent()
    report = await orchestrator.run_investigation(es_client, GITHUB_REPO)

    if report.get("status") == "no_anomaly":
        return JSONResponse(
            content={"status": "no_anomaly", "message": "No anomaly detected"},
            status_code=200,
        )

    background_tasks.add_task(_store_incident, report)
    background_tasks.add_task(post_incident_alert, report)

    return JSONResponse(content=report, status_code=200)


@app.post("/approve/{incident_id}")
async def approve_rollback(incident_id: str):
    """Approve a rollback for an incident (called from Slack button)."""
    try:
        resp = await es_client.get(index=INCIDENTS_INDEX, id=incident_id)
        incident = resp["_source"]
    except Exception:
        return JSONResponse(
            content={"error": f"Incident {incident_id} not found"},
            status_code=404,
        )

    now = datetime.now(timezone.utc).isoformat()
    await es_client.update(
        index=INCIDENTS_INDEX,
        id=incident_id,
        body={"doc": {"status": "approved", "resolved_at": now, "resolved_by": "slack_approval"}},
    )

    print(f"[Rollback] Triggered mock rollback for incident {incident_id}")
    await post_resolution(incident_id, "slack_approval")

    return JSONResponse(
        content={"status": "rollback_triggered", "incident_id": incident_id},
        status_code=200,
    )


@app.post("/dismiss/{incident_id}")
async def dismiss_incident(incident_id: str):
    """Dismiss an incident (called from Slack button)."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        await es_client.update(
            index=INCIDENTS_INDEX,
            id=incident_id,
            body={"doc": {"status": "dismissed", "resolved_at": now, "resolved_by": "slack_dismiss"}},
        )
    except Exception:
        return JSONResponse(
            content={"error": f"Incident {incident_id} not found"},
            status_code=404,
        )

    return JSONResponse(
        content={"status": "dismissed", "incident_id": incident_id},
        status_code=200,
    )


@app.get("/incidents")
async def list_incidents():
    """Return the last 20 incidents from the sentinel-incidents index."""
    try:
        resp = await es_client.search(
            index=INCIDENTS_INDEX,
            body={
                "size": 20,
                "sort": [{"timestamp": {"order": "desc"}}],
                "_source": [
                    "incident_id", "status", "severity", "root_cause",
                    "timestamp", "resolved_at", "recommended_action",
                ],
            },
        )
        incidents = [hit["_source"] for hit in resp["hits"]["hits"]]
        return JSONResponse(content={"incidents": incidents, "total": len(incidents)})
    except Exception as e:
        return JSONResponse(content={"incidents": [], "error": str(e)}, status_code=200)


@app.get("/health")
async def health_check():
    """Return system health: ES status, agent status, last anomaly check."""
    es_status = "unknown"
    try:
        health = await es_client.cluster.health()
        es_status = health.get("status", "unknown")
    except Exception:
        es_status = "disconnected"

    return JSONResponse(content={
        "elasticsearch": es_status,
        "agents": "ready",
        "last_anomaly_check": last_anomaly_check,
        "poll_interval_seconds": POLL_INTERVAL,
    })


@app.get("/activity-log")
async def get_activity_log():
    """Return the agent activity log (what each agent did and found)."""
    return JSONResponse(content={
        "log": AGENT_ACTIVITY_LOG[-100:],
        "total_entries": len(AGENT_ACTIVITY_LOG),
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.main:app",
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
        reload=True,
    )
