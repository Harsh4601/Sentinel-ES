"""Continuously generate synthetic telemetry and push to Elasticsearch."""

import asyncio
import random
from datetime import datetime, timezone

from elasticsearch import AsyncElasticsearch

ES_HOST = "http://localhost:9200"

SERVICES = ["payment-service", "auth-service", "order-service", "notification-service"]
ENDPOINTS = ["/api/checkout", "/api/login", "/api/orders", "/api/notify", "/api/health"]
ERROR_MESSAGES = [
    "ConnectionRefusedError: database unreachable",
    "TimeoutError: upstream service timeout",
    "KeyError: 'user_id' missing from payload",
    "MemoryError: allocation failed",
    "ValueError: invalid input",
]


async def emit_metrics(es: AsyncElasticsearch, anomaly_mode: bool = False):
    """Emit a single batch of metrics. If anomaly_mode is True, inject 500-error spike."""
    now = datetime.now(timezone.utc)
    docs = []

    for service in SERVICES:
        endpoint = random.choice(ENDPOINTS)

        docs.append({
            "timestamp": now.isoformat(),
            "status_code": 200,
            "count": random.randint(80, 150),
            "service": service,
            "endpoint": endpoint,
            "response_time_ms": random.uniform(50, 200),
        })

        error_count = random.randint(40, 120) if anomaly_mode else random.randint(0, 3)
        docs.append({
            "timestamp": now.isoformat(),
            "status_code": 500,
            "count": error_count,
            "service": service,
            "endpoint": endpoint,
            "response_time_ms": random.uniform(500, 3000) if anomaly_mode else random.uniform(50, 200),
        })

    for doc in docs:
        await es.index(index="app-metrics", document=doc)

    return len(docs)


async def emit_error(es: AsyncElasticsearch):
    """Emit a single APM error event."""
    now = datetime.now(timezone.utc)
    service = random.choice(SERVICES)
    error_msg = random.choice(ERROR_MESSAGES)

    doc = {
        "timestamp": now.isoformat(),
        "error_type": error_msg.split(":")[0],
        "message": error_msg,
        "service": service,
        "stack_trace": f"Traceback:\n  File /app/{service}/handler.py, line {random.randint(10, 300)}\n{error_msg}",
        "severity": random.choice(["error", "critical"]),
        "host": f"pod-{service}-{random.randint(1, 5)}",
    }
    await es.index(index="apm-errors", document=doc)


async def main():
    print("🔄 Starting synthetic telemetry generator...")
    print("   Emitting metrics every 5 seconds. Press Ctrl+C to stop.\n")

    es = AsyncElasticsearch([ES_HOST])

    try:
        info = await es.info()
        print(f"  ✓ Connected to Elasticsearch {info['version']['number']}")
    except Exception as e:
        print(f"  ✗ Connection failed: {e}")
        return

    cycle = 0
    try:
        while True:
            cycle += 1
            # Inject anomaly every 12th cycle (~1 minute of anomaly per hour)
            anomaly = cycle % 60 >= 48

            count = await emit_metrics(es, anomaly_mode=anomaly)
            if anomaly:
                await emit_error(es)
                print(f"  [{datetime.now(timezone.utc).strftime('%H:%M:%S')}] 🚨 ANOMALY — emitted {count} metrics + error")
            else:
                print(f"  [{datetime.now(timezone.utc).strftime('%H:%M:%S')}] ✓ Normal — emitted {count} metrics")

            await asyncio.sleep(5)
    except KeyboardInterrupt:
        print("\n\n🛑 Stopped telemetry generator.")
    finally:
        await es.close()


if __name__ == "__main__":
    asyncio.run(main())
