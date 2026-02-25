"""Sentinel-ES Demo Script — End-to-end demonstration with rich terminal output."""

import asyncio
import os
import sys
import time

from dotenv import load_dotenv
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

load_dotenv()

console = Console()

ES_HOST = os.getenv("ES_HOST", "http://localhost:9200")


async def check_elasticsearch():
    """Verify Elasticsearch is running."""
    from elasticsearch import AsyncElasticsearch

    es = AsyncElasticsearch([ES_HOST])
    try:
        info = await es.info()
        version = info["version"]["number"]
        console.print(f"  [green]✓[/green] Connected to Elasticsearch {version}")
        return es
    except Exception as e:
        console.print(f"  [red]✗[/red] Cannot connect to Elasticsearch at {ES_HOST}")
        console.print(f"    Error: {e}")
        console.print("\n  [yellow]Make sure to run:[/yellow] docker compose up -d")
        return None


async def reseed_data(es):
    """Re-seed Elasticsearch with fresh anomaly data."""
    sys.path.insert(0, os.path.dirname(__file__))
    from ingestion.seed_elasticsearch import (
        create_indices,
        seed_apm_errors,
        seed_app_metrics,
        seed_runbooks,
    )

    await create_indices(es)
    await seed_apm_errors(es)
    await seed_app_metrics(es)
    await seed_runbooks(es)


async def run_demo():
    """Execute the full demo pipeline."""
    console.print()
    console.print(Panel.fit(
        "[bold cyan]Sentinel-ES[/bold cyan]\n"
        "[dim]Autonomous SRE · Multi-Agent System[/dim]\n"
        "[dim]Powered by Elasticsearch + Groq LLM[/dim]",
        border_style="cyan",
    ))
    console.print()

    # Step 1: Check ES
    console.print("[bold]Step 1:[/bold] Connecting to Elasticsearch...")
    es = await check_elasticsearch()
    if not es:
        return
    console.print()

    # Step 2: Seed data
    console.print("[bold]Step 2:[/bold] Seeding data with anomaly spike...")
    with console.status("[cyan]Seeding indices...", spinner="dots"):
        await reseed_data(es)
    console.print("  [green]✓[/green] Data seeded with HTTP 500 spike at T-30min\n")

    # Step 3: Run orchestrator
    console.print("[bold]Step 3:[/bold] Running investigation pipeline...\n")

    from agents.orchestrator import OrchestratorAgent

    orchestrator = OrchestratorAgent()
    start_time = time.time()

    # Anomaly detection
    with console.status("[cyan]  Orchestrator → Detecting anomalies...", spinner="dots"):
        from tools.esql_tool import detect_anomalies
        anomaly = await detect_anomalies(es)

    if anomaly.get("anomaly"):
        console.print(f"  [red]🚨 ANOMALY DETECTED[/red]")
        console.print(f"     Current rate: [bold]{anomaly['current_rate']}[/bold] errors/window")
        console.print(f"     Baseline:     [bold]{anomaly['baseline_rate']}[/bold] errors/window")
        console.print(f"     Spike ratio:  [bold]{anomaly['current_rate'] / max(anomaly['baseline_rate'], 0.01):.1f}x[/bold] above baseline")
        console.print()
    else:
        console.print("  [green]✓[/green] No anomaly detected. System is healthy.")
        console.print(f"     Current rate: {anomaly.get('current_rate', 0)}, Baseline: {anomaly.get('baseline_rate', 0)}")
        await es.close()
        return

    # Full investigation
    with console.status("[cyan]  Sleuth Agent → Investigating APM errors...", spinner="dots"):
        sleuth_findings = await orchestrator.sleuth.investigate(es)

    console.print(f"  [yellow]🔍 Sleuth[/yellow] → Primary error: [bold]{sleuth_findings.get('primary_error', 'N/A')}[/bold]")
    console.print(f"     Service: {sleuth_findings.get('affected_service', 'N/A')}")
    console.print(f"     Cause: {sleuth_findings.get('likely_cause', 'N/A')}")
    console.print(f"     Confidence: {sleuth_findings.get('confidence', 'N/A')}")
    console.print()

    primary_error = sleuth_findings.get("primary_error", "Unknown error")

    with console.status("[cyan]  Historian Agent → Analyzing git commits...", spinner="dots"):
        historian_findings = await orchestrator.historian.find_culprit_commit(primary_error)

    console.print(f"  [blue]📜 Historian[/blue] → Culprit: [bold]{historian_findings.get('culprit_commit_sha', 'N/A')}[/bold]")
    console.print(f"     Author: {historian_findings.get('author', 'N/A')}")
    console.print(f"     Reasoning: {historian_findings.get('reasoning', 'N/A')[:120]}")
    console.print(f"     Confidence: {historian_findings.get('confidence', 'N/A')}")
    console.print()

    with console.status("[cyan]  Scribe Agent → Searching runbooks...", spinner="dots"):
        scribe_findings = await orchestrator.scribe.find_runbook(es, primary_error)

    console.print(f"  [green]📚 Scribe[/green] → Matched: {', '.join(scribe_findings.get('matched_runbooks', ['None']))}")
    console.print(f"     Rollback possible: {'Yes' if scribe_findings.get('rollback_possible') else 'No'}")
    console.print(f"     Est. fix time: {scribe_findings.get('estimated_fix_time', 'unknown')}")
    for i, step in enumerate(scribe_findings.get("recommended_steps", []), 1):
        console.print(f"     {i}. {step}")
    console.print()

    # Synthesis
    with console.status("[cyan]  Orchestrator → Synthesizing findings...", spinner="dots"):
        synthesis = await orchestrator._resolve_conflicts(sleuth_findings, historian_findings, scribe_findings)

    elapsed = time.time() - start_time

    # Final report
    console.print(Panel(
        f"[bold]Severity:[/bold] {synthesis.get('severity', 'P2')}\n"
        f"[bold]Root Cause:[/bold] {synthesis.get('root_cause', 'Unknown')}\n"
        f"[bold]Action:[/bold] {synthesis.get('recommended_action', 'N/A')}\n"
        f"[bold]Duration:[/bold] {elapsed:.1f}s",
        title="[bold red]Incident Report[/bold red]",
        border_style="red",
    ))

    # Slack preview
    slack_msg = orchestrator._format_slack_message(
        "demo-001", synthesis, sleuth_findings, historian_findings, scribe_findings
    )
    console.print()
    console.print(Panel(
        slack_msg.replace("*", "[bold]").replace("_", "[italic]"),
        title="[bold]Slack Message Preview[/bold]",
        border_style="blue",
    ))

    # Agent activity log
    from agents.base_agent import AGENT_ACTIVITY_LOG
    if AGENT_ACTIVITY_LOG:
        console.print()
        table = Table(title="Agent Activity Log", show_lines=True)
        table.add_column("Timestamp", style="dim", width=20)
        table.add_column("Agent", style="cyan")
        table.add_column("Action", style="green")
        table.add_column("Result", max_width=60)

        for entry in AGENT_ACTIVITY_LOG[-10:]:
            table.add_row(
                entry["timestamp"][11:19],
                entry["agent"],
                entry["action"],
                entry["result"][:60],
            )
        console.print(table)

    await es.close()
    console.print(f"\n[green]✅ Demo complete![/green] Total time: {elapsed:.1f}s\n")


if __name__ == "__main__":
    asyncio.run(run_demo())
