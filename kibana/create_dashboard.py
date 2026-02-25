"""Programmatically create Kibana dashboard for Sentinel-ES agent activity."""

import json
import sys
from datetime import datetime, timezone

import httpx

KIBANA_URL = "http://localhost:5601"
HEADERS = {"kbn-xsrf": "true", "Content-Type": "application/json"}


def build_dashboard_ndjson() -> str:
    """Build the NDJSON payload for a Kibana saved objects import."""

    # Data view for app-metrics
    metrics_data_view = {
        "type": "index-pattern",
        "id": "app-metrics-pattern",
        "attributes": {
            "title": "app-metrics",
            "timeFieldName": "timestamp",
        },
        "references": [],
    }

    # Data view for sentinel-incidents
    incidents_data_view = {
        "type": "index-pattern",
        "id": "sentinel-incidents-pattern",
        "attributes": {
            "title": "sentinel-incidents",
            "timeFieldName": "timestamp",
        },
        "references": [],
    }

    # 1. Line chart: HTTP 500 error rate over time
    error_rate_vis = {
        "type": "visualization",
        "id": "sentinel-500-error-rate",
        "attributes": {
            "title": "HTTP 500 Error Rate Over Time",
            "visState": json.dumps({
                "title": "HTTP 500 Error Rate Over Time",
                "type": "line",
                "aggs": [
                    {
                        "id": "1",
                        "enabled": True,
                        "type": "sum",
                        "params": {"field": "count"},
                        "schema": "metric",
                    },
                    {
                        "id": "2",
                        "enabled": True,
                        "type": "date_histogram",
                        "params": {
                            "field": "timestamp",
                            "interval": "auto",
                            "min_doc_count": 0,
                        },
                        "schema": "segment",
                    },
                ],
                "params": {
                    "type": "line",
                    "grid": {"categoryLines": False},
                    "categoryAxes": [{"id": "CategoryAxis-1", "type": "category", "position": "bottom"}],
                    "valueAxes": [{"id": "ValueAxis-1", "name": "LeftAxis-1", "type": "value", "position": "left"}],
                },
            }),
            "uiStateJSON": "{}",
            "description": "Shows HTTP 500 error count over the last 2 hours with anomaly spike visible",
            "kibanaSavedObjectMeta": {
                "searchSourceJSON": json.dumps({
                    "index": "app-metrics-pattern",
                    "query": {"query": "status_code: 500", "language": "kuery"},
                    "filter": [],
                }),
            },
        },
        "references": [
            {"id": "app-metrics-pattern", "name": "kibanaSavedObjectMeta.searchSourceJSON.index", "type": "index-pattern"},
        ],
    }

    # 2. Data table: Recent incidents
    incidents_table_vis = {
        "type": "visualization",
        "id": "sentinel-incidents-table",
        "attributes": {
            "title": "Recent Incidents",
            "visState": json.dumps({
                "title": "Recent Incidents",
                "type": "table",
                "aggs": [
                    {"id": "1", "enabled": True, "type": "count", "params": {}, "schema": "metric"},
                    {"id": "2", "enabled": True, "type": "terms", "params": {"field": "incident_id", "size": 20, "order": "desc", "orderBy": "_key"}, "schema": "bucket"},
                    {"id": "3", "enabled": True, "type": "terms", "params": {"field": "severity", "size": 5}, "schema": "bucket"},
                    {"id": "4", "enabled": True, "type": "terms", "params": {"field": "status", "size": 5}, "schema": "bucket"},
                ],
                "params": {"perPage": 10, "showPartialRows": False, "showTotal": False},
            }),
            "uiStateJSON": "{}",
            "description": "Table showing recent incidents with severity, status, and resolution info",
            "kibanaSavedObjectMeta": {
                "searchSourceJSON": json.dumps({
                    "index": "sentinel-incidents-pattern",
                    "query": {"query": "", "language": "kuery"},
                    "filter": [],
                }),
            },
        },
        "references": [
            {"id": "sentinel-incidents-pattern", "name": "kibanaSavedObjectMeta.searchSourceJSON.index", "type": "index-pattern"},
        ],
    }

    # 3. Metric tile: Incidents auto-resolved count
    resolved_metric_vis = {
        "type": "visualization",
        "id": "sentinel-resolved-count",
        "attributes": {
            "title": "Incidents Auto-Resolved",
            "visState": json.dumps({
                "title": "Incidents Auto-Resolved",
                "type": "metric",
                "aggs": [
                    {"id": "1", "enabled": True, "type": "count", "params": {}, "schema": "metric"},
                ],
                "params": {
                    "addTooltip": True,
                    "addLegend": False,
                    "type": "metric",
                    "metric": {"colorSchema": "Green to Red", "labels": {"show": True}},
                },
            }),
            "uiStateJSON": "{}",
            "description": "Count of incidents with status=approved (resolved)",
            "kibanaSavedObjectMeta": {
                "searchSourceJSON": json.dumps({
                    "index": "sentinel-incidents-pattern",
                    "query": {"query": "status: approved", "language": "kuery"},
                    "filter": [],
                }),
            },
        },
        "references": [
            {"id": "sentinel-incidents-pattern", "name": "kibanaSavedObjectMeta.searchSourceJSON.index", "type": "index-pattern"},
        ],
    }

    # 4. Pie chart: Incident severity distribution
    severity_pie_vis = {
        "type": "visualization",
        "id": "sentinel-severity-pie",
        "attributes": {
            "title": "Incident Severity Distribution",
            "visState": json.dumps({
                "title": "Incident Severity Distribution",
                "type": "pie",
                "aggs": [
                    {"id": "1", "enabled": True, "type": "count", "params": {}, "schema": "metric"},
                    {"id": "2", "enabled": True, "type": "terms", "params": {"field": "severity", "size": 5}, "schema": "segment"},
                ],
                "params": {"type": "pie", "addTooltip": True, "addLegend": True, "isDonut": True},
            }),
            "uiStateJSON": "{}",
            "description": "Distribution of incident severity (P1/P2/P3)",
            "kibanaSavedObjectMeta": {
                "searchSourceJSON": json.dumps({
                    "index": "sentinel-incidents-pattern",
                    "query": {"query": "", "language": "kuery"},
                    "filter": [],
                }),
            },
        },
        "references": [
            {"id": "sentinel-incidents-pattern", "name": "kibanaSavedObjectMeta.searchSourceJSON.index", "type": "index-pattern"},
        ],
    }

    # 5. Markdown panel: Agent activity
    activity_markdown_vis = {
        "type": "visualization",
        "id": "sentinel-agent-activity",
        "attributes": {
            "title": "Sentinel-ES Agent Activity",
            "visState": json.dumps({
                "title": "Sentinel-ES Agent Activity",
                "type": "markdown",
                "params": {
                    "markdown": (
                        "## Sentinel-ES Agent Activity\n\n"
                        "This panel shows the latest agent investigation activity.\n\n"
                        "| Agent | Action | Status |\n"
                        "|-------|--------|--------|\n"
                        "| Orchestrator | Anomaly Detection | Running |\n"
                        "| Sleuth | APM Error Investigation | Ready |\n"
                        "| Historian | Git Commit Analysis | Ready |\n"
                        "| Scribe | Runbook Search | Ready |\n\n"
                        "*Updated via `/activity-log` API endpoint*"
                    ),
                    "fontSize": 12,
                },
                "aggs": [],
            }),
            "uiStateJSON": "{}",
            "description": "Markdown panel showing agent activity status",
            "kibanaSavedObjectMeta": {
                "searchSourceJSON": json.dumps({"query": {"query": "", "language": "kuery"}, "filter": []}),
            },
        },
        "references": [],
    }

    # Dashboard that assembles all visualizations
    dashboard = {
        "type": "dashboard",
        "id": "sentinel-es-dashboard",
        "attributes": {
            "title": "Sentinel-ES: SRE Agent Dashboard",
            "description": "Real-time view of Sentinel-ES autonomous SRE agent activity, incidents, and error rates",
            "panelsJSON": json.dumps([
                {"version": "8.13.0", "type": "visualization", "gridData": {"x": 0, "y": 0, "w": 48, "h": 15, "i": "1"}, "panelIndex": "1", "embeddableConfig": {}, "panelRefName": "panel_0"},
                {"version": "8.13.0", "type": "visualization", "gridData": {"x": 0, "y": 15, "w": 24, "h": 15, "i": "2"}, "panelIndex": "2", "embeddableConfig": {}, "panelRefName": "panel_1"},
                {"version": "8.13.0", "type": "visualization", "gridData": {"x": 24, "y": 15, "w": 12, "h": 8, "i": "3"}, "panelIndex": "3", "embeddableConfig": {}, "panelRefName": "panel_2"},
                {"version": "8.13.0", "type": "visualization", "gridData": {"x": 36, "y": 15, "w": 12, "h": 8, "i": "4"}, "panelIndex": "4", "embeddableConfig": {}, "panelRefName": "panel_3"},
                {"version": "8.13.0", "type": "visualization", "gridData": {"x": 24, "y": 23, "w": 24, "h": 7, "i": "5"}, "panelIndex": "5", "embeddableConfig": {}, "panelRefName": "panel_4"},
            ]),
            "optionsJSON": json.dumps({"useMargins": True, "syncColors": False, "syncCursor": True, "syncTooltips": False, "hidePanelTitles": False}),
            "timeRestore": True,
            "timeTo": "now",
            "timeFrom": "now-2h",
            "refreshInterval": {"pause": False, "value": 30000},
            "kibanaSavedObjectMeta": {
                "searchSourceJSON": json.dumps({"query": {"query": "", "language": "kuery"}, "filter": []}),
            },
        },
        "references": [
            {"id": "sentinel-500-error-rate", "name": "panel_0", "type": "visualization"},
            {"id": "sentinel-incidents-table", "name": "panel_1", "type": "visualization"},
            {"id": "sentinel-resolved-count", "name": "panel_2", "type": "visualization"},
            {"id": "sentinel-severity-pie", "name": "panel_3", "type": "visualization"},
            {"id": "sentinel-agent-activity", "name": "panel_4", "type": "visualization"},
        ],
    }

    objects = [
        metrics_data_view,
        incidents_data_view,
        error_rate_vis,
        incidents_table_vis,
        resolved_metric_vis,
        severity_pie_vis,
        activity_markdown_vis,
        dashboard,
    ]

    return "\n".join(json.dumps(obj) for obj in objects)


def export_ndjson(filepath: str = "kibana/dashboard_export.ndjson"):
    """Write the dashboard NDJSON to a file."""
    ndjson = build_dashboard_ndjson()
    with open(filepath, "w") as f:
        f.write(ndjson + "\n")
    print(f"  ✓ Exported dashboard to {filepath}")


async def import_to_kibana():
    """Import the dashboard via Kibana Saved Objects API."""
    ndjson = build_dashboard_ndjson()
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{KIBANA_URL}/api/saved_objects/_import",
                headers={"kbn-xsrf": "true"},
                files={"file": ("dashboard.ndjson", ndjson.encode(), "application/x-ndjson")},
                params={"overwrite": "true"},
                timeout=30,
            )
            if resp.status_code == 200:
                result = resp.json()
                print(f"  ✓ Imported {result.get('successCount', 0)} objects to Kibana")
                if result.get("errors"):
                    for err in result["errors"]:
                        print(f"    ⚠ {err.get('id')}: {err.get('error', {}).get('message', 'unknown error')}")
                return True
            else:
                print(f"  ✗ Kibana import failed: {resp.status_code}")
                print(f"    {resp.text[:200]}")
                return False
    except Exception as e:
        print(f"  ✗ Could not connect to Kibana: {e}")
        return False


def main():
    """Export and optionally import the dashboard."""
    import asyncio

    print("\n📊 Sentinel-ES Kibana Dashboard Setup\n")

    export_ndjson()

    print("\n  Attempting API import to Kibana...")
    success = asyncio.run(import_to_kibana())

    if not success:
        print("\n  📋 Manual Import Instructions:")
        print("  1. Open Kibana: http://localhost:5601")
        print("  2. Go to Stack Management → Saved Objects")
        print("  3. Click 'Import' and select kibana/dashboard_export.ndjson")
        print("  4. Choose 'Overwrite' if prompted")
        print("  5. Navigate to Dashboard → 'Sentinel-ES: SRE Agent Dashboard'")

    print("\n✅ Dashboard setup complete!")


if __name__ == "__main__":
    main()
