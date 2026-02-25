# Sentinel-ES

**Autonomous SRE powered by Elasticsearch Agent Builder**

![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue.svg)
![Elasticsearch 8.x](https://img.shields.io/badge/Elasticsearch-8.x-yellow.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)

---

## The Problem

SRE teams drown in alerts — 70% are noise, and the real incidents get lost in the flood. Mean Time to Resolution (MTTR) suffers because engineers waste minutes context-switching between logs, metrics, git history, and runbooks. Sentinel-ES eliminates that toil by autonomously detecting anomalies, investigating root causes across disconnected systems, and delivering a unified incident report with one-click remediation — all before a human even opens a terminal.

---

## Architecture

```
                          ┌─────────────────────────┐
                          │   Elasticsearch 8.x     │
                          │  (app-metrics, apm-errors│
                          │   runbooks, incidents)   │
                          └──────────┬──────────────┘
                                     │
                              ES|QL Anomaly
                              Detection (3x spike)
                                     │
                          ┌──────────▼──────────────┐
                          │     Orchestrator Agent    │
                          │   (Incident Commander)    │
                          └──┬───────┬───────┬──────┘
                             │       │       │
                    ┌────────▼┐  ┌───▼────┐  ┌▼────────┐
                    │ Sleuth   │  │Historian│  │ Scribe   │
                    │ (APM     │  │(Git     │  │(Runbook  │
                    │ Errors)  │  │Commits) │  │Research) │
                    └────┬─────┘  └───┬────┘  └───┬─────┘
                         │            │            │
                         └────────┬───┘────────────┘
                                  │
                          ┌───────▼────────────────┐
                          │   Conflict Resolution   │
                          │  (LLM Synthesis Step)   │
                          └───────┬────────────────┘
                                  │
                    ┌─────────────▼──────────────────┐
                    │        Slack Alert              │
                    │  [✅ Approve Rollback] [❌ Dismiss]│
                    └─────────────┬──────────────────┘
                                  │
                          ┌───────▼────────────────┐
                          │   Human Approval →      │
                          │   Rollback / Resolve    │
                          └─────────────────────────┘
```

---

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/your-org/sentinel-es.git
cd sentinel-es

# 2. Configure environment
cp .env.example .env
# Edit .env with your GROQ_API_KEY (free at https://console.groq.com)

# 3. Start Elasticsearch + Kibana
docker compose up -d

# 4. Install dependencies
pip install -r requirements.txt

# 5. Seed data and run the demo
python ingestion/seed_elasticsearch.py
python demo.py
```

---

## How It Works

### Orchestrator Agent
The Orchestrator is the incident commander. It first checks for anomalies using ES|QL (comparing the last 30 minutes of HTTP 500 errors against a 2-hour baseline). If the error rate exceeds 3x the baseline, it launches three specialist agents in parallel, synthesizes their findings, resolves any conflicts, and produces a unified incident report.

### Sleuth Agent (APM Error Investigator)
The Sleuth pulls the top 5 most frequent error types from the `apm-errors` Elasticsearch index, formats them into a structured prompt, and asks the LLM to identify the primary error pattern, affected service, and likely root cause. It returns a confidence-scored finding.

### Historian Agent (Git Commit Investigator)
The Historian fetches recent commits from GitHub (or uses realistic mock data for demos) and correlates them with the error summary from the Sleuth. The LLM identifies which commit most likely introduced the bug, with reasoning about why that deployment is the culprit.

### Scribe Agent (Runbook Researcher)
The Scribe searches the `runbooks` Elasticsearch index using keyword matching against the error summary. It sends the top 2 matching runbook documents to the LLM and produces exactly 3 actionable remediation steps, along with rollback feasibility and estimated fix time.

### Safety Guardrails
Every action passes through the `ActionGuardrail` system. Destructive operations (deployments, DB writes, secret rotation) are always blocked unless explicitly approved. P1 incidents and rollback-eligible incidents always require human approval via Slack buttons. ES|QL queries are validated to reject DELETE/UPDATE/DROP operations.

---

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/webhook/alert` | POST | Trigger anomaly investigation |
| `/approve/{incident_id}` | POST | Approve rollback (Slack callback) |
| `/dismiss/{incident_id}` | POST | Dismiss incident (Slack callback) |
| `/incidents` | GET | List last 20 incidents |
| `/health` | GET | System health check |
| `/activity-log` | GET | Agent activity audit trail |

---

## Hackathon Submission Checklist

| Requirement | How Sentinel-ES covers it |
|---|---|
| Multi-step agent | Orchestrator → Sleuth → Historian → Scribe → Slack |
| Uses ES\|QL | Anomaly detection and APM error queries |
| Search tool | Keyword-based runbook search in Elasticsearch |
| Time-series aware | HTTP 500 rate comparison over rolling time windows |
| Connects disconnected systems | ES + GitHub + Slack integrated |
| Agents take reliable action | Slack approval flow before any write action |
| Multi-agent coordination | 3 specialist agents + LLM conflict resolution |
| Measurable impact | MTTR dashboard in Kibana |
| Embeds where work happens | Native Slack integration with action buttons |
| Human-in-the-loop | Approve/Dismiss buttons, strict guardrails module |

---

## 6-Month Roadmap

| Month | Goal | Milestone |
|---|---|---|
| 1 | Core MVP | ES|QL anomaly detection, 3 agents, Slack alerts, Kibana dashboard |
| 2 | Memory & Learning | Incident memory index, cross-session pattern recognition |
| 3 | Auto-Remediation | Draft PR creation, automated rollback with approval gates |
| 4 | Multi-Cluster | Support for multiple ES clusters, federated incident management |
| 5 | Advanced Analytics | ML-based anomaly detection, predictive alerting |
| 6 | Enterprise Ready | SSO integration, audit logging, RBAC, SLA tracking |

---

## Project Structure

```
sentinel-es/
├── docker-compose.yml          # Elasticsearch + Kibana (local dev)
├── .env.example                # Environment variable reference
├── requirements.txt            # Python dependencies
├── demo.py                     # Interactive demo script
├── ingestion/
│   ├── fake_metrics.py         # Continuous synthetic telemetry generator
│   └── seed_elasticsearch.py   # Seed ES with sample APM/log data
├── agents/
│   ├── base_agent.py           # Shared LLM + Groq wrapper
│   ├── sleuth_agent.py         # APM error pattern investigator
│   ├── historian_agent.py      # Git commit correlator
│   ├── scribe_agent.py         # Runbook search & remediation
│   └── orchestrator.py         # Multi-agent coordinator
├── tools/
│   ├── esql_tool.py            # ES|QL query execution & anomaly detection
│   ├── github_tool.py          # GitHub API (commits, draft PRs)
│   └── slack_tool.py           # Slack Incoming Webhooks
├── api/
│   └── main.py                 # FastAPI application
├── safety/
│   └── guardrails.py           # Human-in-the-loop enforcement
├── agent_builder/
│   ├── sentinel_agent_config.py    # Agent Builder config generator
│   └── sentinel_agent_config.json  # Exported config for Kibana import
├── kibana/
│   ├── create_dashboard.py     # Dashboard creation script
│   └── dashboard_export.ndjson # Importable Kibana dashboard
└── tests/
    └── test_agents.py          # Pytest test suite (6 tests)
```

---

## Running Tests

```bash
python -m pytest tests/test_agents.py -v
```

All tests use mocked ES and LLM responses — no external services required.

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes and add tests
4. Run the test suite: `python -m pytest tests/ -v`
5. Submit a pull request

---

## License

MIT
