"""Seed Elasticsearch with sample APM errors, metrics, and runbook data."""

import asyncio
import random
from datetime import datetime, timedelta, timezone

from elasticsearch import AsyncElasticsearch

ES_HOST = "http://localhost:9200"

PYTHON_EXCEPTIONS = [
    ("ConnectionRefusedError", "Connection refused: [Errno 111] Connection refused to database at 10.0.3.14:5432"),
    ("MemoryError", "MemoryError: Unable to allocate 2.14 GiB for an array with shape (287309824,)"),
    ("TimeoutError", "TimeoutError: HTTPSConnectionPool(host='payment-api.internal', port=443): Read timed out. (read timeout=30)"),
    ("KeyError", "KeyError: 'user_id' in /app/services/auth.py line 142"),
    ("ValueError", "ValueError: invalid literal for int() with base 10: 'null' in /app/api/orders.py line 87"),
    ("AttributeError", "AttributeError: 'NoneType' object has no attribute 'process' in /app/workers/pipeline.py line 203"),
    ("IntegrityError", "sqlalchemy.exc.IntegrityError: (psycopg2.errors.UniqueViolation) duplicate key value violates unique constraint \"users_email_key\""),
    ("RuntimeError", "RuntimeError: maximum recursion depth exceeded in /app/utils/tree.py line 45"),
]

NODEJS_ERRORS = [
    ("TypeError", "TypeError: Cannot read properties of undefined (reading 'map') at /app/routes/dashboard.js:89"),
    ("RangeError", "RangeError: Maximum call stack size exceeded at Object.stringify (<anonymous>)"),
    ("SyntaxError", "SyntaxError: Unexpected token '<' in JSON at position 0 at /app/middleware/parser.js:22"),
    ("ECONNRESET", "Error: read ECONNRESET at TCP.onStreamRead (internal/stream_base_commons.js:209:20)"),
]

SERVICES = ["payment-service", "auth-service", "order-service", "notification-service", "dashboard-api"]

STACK_TEMPLATES = {
    "python": """Traceback (most recent call last):
  File "/app/main.py", line {line1}, in handle_request
    result = await process_request(request)
  File "/app/services/{service}.py", line {line2}, in process_request
    data = await fetch_data(params)
  File "/app/utils/db.py", line {line3}, in fetch_data
    return await connection.execute(query)
{error}: {message}""",
    "nodejs": """Error: {message}
    at Object.<anonymous> (/app/routes/{service}.js:{line1}:15)
    at Module._compile (internal/modules/cjs/loader.js:1085:14)
    at processTicksAndRejections (internal/process/task_queues.js:95:5)
    at async Router.handle (/app/node_modules/express/lib/router/index.js:{line2}:5)""",
}

RUNBOOKS = [
    {
        "title": "Runbook: Database Connection Timeout",
        "tags": ["database", "timeout", "connection", "postgres", "mysql"],
        "content": """# Database Connection Timeout

## Symptoms
- Application logs show ConnectionRefusedError or TimeoutError to database hosts
- HTTP 500 errors spike on endpoints that require database reads/writes
- Connection pool exhaustion warnings in application metrics

## Diagnosis Steps
1. Check database host health: `pg_isready -h <host> -p 5432`
2. Review connection pool metrics in Kibana dashboard
3. Check if recent deployments changed connection pool settings
4. Verify network connectivity between app pods and database

## Remediation
1. **Immediate**: Restart application pods to reset connection pool: `kubectl rollout restart deployment/<service>`
2. **If pool exhaustion**: Increase max pool size in config (default: 10 → 25)
3. **If DB host down**: Failover to read replica, page DBA team
4. **Prevention**: Add connection health check middleware, implement circuit breaker

## Escalation
- If not resolved in 15 minutes, page the DBA on-call
- If customer-facing impact > 5 minutes, declare P1 incident
""",
    },
    {
        "title": "Runbook: OOM Kill / Memory Exhaustion",
        "tags": ["memory", "oom", "kill", "kubernetes", "pod"],
        "content": """# OOM Kill / Memory Exhaustion

## Symptoms
- Pods restarting with OOMKilled status
- MemoryError exceptions in application logs
- Gradual memory increase visible in Grafana/Kibana dashboards

## Diagnosis Steps
1. Check pod status: `kubectl get pods | grep OOMKilled`
2. Review memory usage trends: last 6 hours in Kibana
3. Check for memory leaks: compare heap dumps between restarts
4. Identify the specific container/process consuming most memory

## Remediation
1. **Immediate**: Increase memory limits in pod spec (request: 512Mi → 1Gi, limit: 1Gi → 2Gi)
2. **If leak suspected**: Rollback to last known good deployment
3. **Quick fix**: Restart pods with `kubectl rollout restart`
4. **Long-term**: Profile memory usage, fix leaking code paths

## Escalation
- If multiple services affected, declare P1
- If single service, P2 with 30-minute resolution target
""",
    },
    {
        "title": "Runbook: HTTP 5xx Error Spike",
        "tags": ["http", "500", "5xx", "error", "spike", "api"],
        "content": """# HTTP 5xx Error Spike

## Symptoms
- Sudden increase in HTTP 500/502/503 responses
- Error rate exceeds 3x baseline in monitoring
- Customer reports of failed requests

## Diagnosis Steps
1. Check error logs in Elasticsearch: filter by status_code >= 500 in last 30 minutes
2. Identify which endpoint(s) are failing using ES|QL aggregation
3. Check recent deployments: `git log --since="2 hours ago" --oneline`
4. Verify upstream dependencies are healthy

## Remediation
1. **If caused by recent deploy**: Rollback immediately using `git revert <sha> && git push`
2. **If upstream dependency**: Enable circuit breaker, return cached/degraded responses
3. **If infrastructure**: Check pod health, node resources, restart if needed
4. **Communication**: Post in #incidents Slack channel, update status page

## Rollback Procedure
1. Identify culprit commit SHA from git log
2. Create rollback branch: `git checkout -b rollback/<incident-id>`
3. Revert the commit: `git revert <sha>`
4. Push and deploy: CI/CD will auto-deploy on merge to main
5. Verify error rate returns to baseline within 5 minutes

## Escalation
- P1 if error rate > 50% of traffic
- P2 if error rate > 10% of traffic
- P3 if isolated to non-critical endpoints
""",
    },
    {
        "title": "Runbook: SSL/TLS Certificate Expiry",
        "tags": ["ssl", "tls", "certificate", "expiry", "https"],
        "content": """# SSL/TLS Certificate Expiry

## Symptoms
- Users see "connection not secure" warnings
- API clients receive SSL handshake failures
- Certificate expiry alerts from monitoring

## Diagnosis Steps
1. Check cert expiry: `echo | openssl s_client -connect <host>:443 2>/dev/null | openssl x509 -noout -dates`
2. Verify cert-manager status in Kubernetes
3. Check if auto-renewal failed in cert-manager logs

## Remediation
1. **Immediate**: Manually renew via cert-manager: `kubectl delete certificate <name> && kubectl apply -f cert.yaml`
2. **If cert-manager broken**: Generate cert manually with certbot
3. **Prevention**: Set up monitoring for certs expiring within 14 days

## Escalation
- P1 if customer-facing domains affected
- P2 if internal services only
""",
    },
    {
        "title": "Runbook: API Rate Limiting / Throttling",
        "tags": ["rate-limit", "throttle", "429", "api", "quota"],
        "content": """# API Rate Limiting / Throttling

## Symptoms
- HTTP 429 Too Many Requests responses increasing
- Third-party API calls failing with quota exceeded
- Batch jobs timing out due to retry backoff

## Diagnosis Steps
1. Check which API is being rate-limited (internal vs external)
2. Review request volume: `ES|QL: FROM app-metrics | WHERE status_code == 429 | STATS count=COUNT(*) BY service`
3. Identify the source of excessive requests (specific service, cron job, or user)

## Remediation
1. **Immediate**: Implement exponential backoff in calling service
2. **If internal API**: Increase rate limit in API gateway config
3. **If external API**: Queue requests, spread load over time window
4. **Prevention**: Add request budgets per service, implement client-side caching

## Escalation
- P3 unless causing cascading failures in critical path
""",
    },
]


async def create_indices(es: AsyncElasticsearch):
    """Create the three main indices with appropriate mappings."""
    indices = {
        "apm-errors": {
            "mappings": {
                "properties": {
                    "timestamp": {"type": "date"},
                    "error_type": {"type": "keyword"},
                    "message": {"type": "text"},
                    "service": {"type": "keyword"},
                    "stack_trace": {"type": "text"},
                    "severity": {"type": "keyword"},
                    "host": {"type": "keyword"},
                }
            }
        },
        "app-metrics": {
            "mappings": {
                "properties": {
                    "timestamp": {"type": "date"},
                    "status_code": {"type": "integer"},
                    "count": {"type": "integer"},
                    "service": {"type": "keyword"},
                    "endpoint": {"type": "keyword"},
                    "response_time_ms": {"type": "float"},
                }
            }
        },
        "runbooks": {
            "mappings": {
                "properties": {
                    "title": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                    "content": {"type": "text"},
                    "tags": {"type": "keyword"},
                    "created_at": {"type": "date"},
                }
            }
        },
    }

    for index_name, body in indices.items():
        if await es.indices.exists(index=index_name):
            await es.indices.delete(index=index_name)
            print(f"  Deleted existing index: {index_name}")
        await es.indices.create(index=index_name, body=body)
        print(f"  ✓ Created index: {index_name}")


async def seed_apm_errors(es: AsyncElasticsearch, count: int = 50):
    """Seed apm-errors with fake stack traces."""
    now = datetime.now(timezone.utc)
    all_errors = PYTHON_EXCEPTIONS + NODEJS_ERRORS
    docs = []

    for i in range(count):
        minutes_ago = random.randint(1, 120)
        ts = now - timedelta(minutes=minutes_ago)
        error_type, message = random.choice(all_errors)
        service = random.choice(SERVICES)
        is_python = error_type in [e[0] for e in PYTHON_EXCEPTIONS]
        lang = "python" if is_python else "nodejs"

        stack = STACK_TEMPLATES[lang].format(
            error=error_type,
            message=message,
            service=service.replace("-", "_"),
            line1=random.randint(20, 200),
            line2=random.randint(20, 200),
            line3=random.randint(20, 200),
        )

        docs.append(
            {
                "timestamp": ts.isoformat(),
                "error_type": error_type,
                "message": message,
                "service": service,
                "stack_trace": stack,
                "severity": random.choice(["error", "critical", "warning"]),
                "host": f"pod-{service}-{random.randint(1,5)}",
            }
        )

    for doc in docs:
        await es.index(index="apm-errors", document=doc)

    await es.indices.refresh(index="apm-errors")
    print(f"  ✓ Seeded apm-errors with {count} documents")


async def seed_app_metrics(es: AsyncElasticsearch):
    """Seed app-metrics with time-series HTTP status code counts, including a 500 spike at T-30min."""
    now = datetime.now(timezone.utc)
    docs = []
    services = ["payment-service", "auth-service", "order-service"]
    endpoints = ["/api/checkout", "/api/login", "/api/orders", "/api/health"]

    for minutes_ago in range(120, 0, -1):
        ts = now - timedelta(minutes=minutes_ago)
        for service in services:
            endpoint = random.choice(endpoints)

            # Normal 200 responses
            docs.append({
                "timestamp": ts.isoformat(),
                "status_code": 200,
                "count": random.randint(80, 150),
                "service": service,
                "endpoint": endpoint,
                "response_time_ms": random.uniform(50, 200),
            })

            # 500 errors: spike between T-35min and T-5min (especially T-30min)
            if 5 <= minutes_ago <= 35:
                error_count = random.randint(40, 120) if 15 <= minutes_ago <= 35 else random.randint(20, 50)
            else:
                error_count = random.randint(0, 3)

            docs.append({
                "timestamp": ts.isoformat(),
                "status_code": 500,
                "count": error_count,
                "service": service,
                "endpoint": endpoint,
                "response_time_ms": random.uniform(500, 3000) if 5 <= minutes_ago <= 35 else random.uniform(50, 200),
            })

    for doc in docs:
        await es.index(index="app-metrics", document=doc)

    await es.indices.refresh(index="app-metrics")
    print(f"  ✓ Seeded app-metrics with {len(docs)} documents (spike at T-30min)")


async def seed_runbooks(es: AsyncElasticsearch):
    """Seed runbooks index with incident response documentation."""
    now = datetime.now(timezone.utc)

    for i, runbook in enumerate(RUNBOOKS):
        doc = {
            "title": runbook["title"],
            "content": runbook["content"],
            "tags": runbook["tags"],
            "created_at": (now - timedelta(days=random.randint(1, 90))).isoformat(),
        }
        await es.index(index="runbooks", document=doc)

    await es.indices.refresh(index="runbooks")
    print(f"  ✓ Seeded runbooks with {len(RUNBOOKS)} documents")


async def main():
    print("\n🔌 Connecting to Elasticsearch at", ES_HOST)
    es = AsyncElasticsearch([ES_HOST])

    try:
        info = await es.info()
        print(f"  ✓ Connected to Elasticsearch {info['version']['number']}\n")
    except Exception as e:
        print(f"  ✗ Failed to connect: {e}")
        print("  Make sure Elasticsearch is running: docker-compose up -d")
        return

    print("📦 Creating indices...")
    await create_indices(es)

    print("\n📊 Seeding data...")
    await seed_apm_errors(es)
    await seed_app_metrics(es)
    await seed_runbooks(es)

    print("\n✅ All indices created and seeded successfully!")
    print("   - apm-errors:  50 error documents")
    print("   - app-metrics: ~720 metric documents (with 500-error spike)")
    print("   - runbooks:    5 runbook documents")

    await es.close()


if __name__ == "__main__":
    asyncio.run(main())
