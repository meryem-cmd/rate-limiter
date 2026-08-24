# Token Bucket Rate Limiter Service

A standalone API rate-limiting service built with Django and Redis. Other services call it to decide, per request, whether a client should be **ALLOWED** or **DENIED** — the same category of problem solved by API gateways at companies like Stripe, GitHub, and Cloudflare.

## Features

- **Two rate-limiting algorithms, selectable per client:**
  - **Token bucket** — allows controlled bursts up to a configurable size, then refills continuously at a configured rate
  - **Sliding window log** — a hard cap on requests within a rolling time window, no burst allowance
- **Atomic, race-condition-free** under concurrent load, using Redis Lua scripting
- **Persistent state** — rate-limit counters live in Redis and survive application restarts
- **Admin API** for configuring per-client limits (requests/sec, burst size, algorithm mode)
- **Standard rate-limit response headers**: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`
- **Prometheus metrics + Grafana dashboard** — live allow/deny rates per client
- Load-tested for concurrent throughput with k6

## Architecture

```
Client → Django REST Framework → Redis (atomic Lua scripts) → ALLOW/DENY
                ↓
         SQLite (per-client config: rate, burst, mode)
                ↓
         Prometheus /metrics → Grafana dashboard
```

- **Redis** holds live, fast-changing state (token counts, sliding window logs) and provides atomic check-and-consume operations via Lua scripts — this is what makes the service correct under concurrency.
- **SQLite (via Django ORM)** holds durable, rarely-changing per-client configuration, cached in-memory for 30s to keep the hot path off the database.
- **Gunicorn**, run with multiple worker processes, serves the app — chosen over a single-process/multi-threaded model specifically to avoid Python's GIL becoming a concurrency bottleneck under load (see Results below).

## Tech stack

- Django + Django REST Framework
- Redis (atomic operations via Lua scripting)
- Gunicorn (multi-process WSGI server)
- Prometheus + Grafana (metrics and dashboard)
- k6 (load testing)

## API

**Admin — configure a client**
```
POST /admin-api/clients/<client_key>/config
Content-Type: application/json

{
  "requests_per_second": 5,
  "burst_size": 10,
  "mode": "token_bucket"   // or "sliding_window"
}
```

**Check — is this request allowed?**
```
GET /check/<client_key>

200 OK   {"decision": "ALLOW"}   (if under the limit)
429      {"decision": "DENY"}   (if over the limit)

Response headers:
  X-RateLimit-Limit: 10
  X-RateLimit-Remaining: 9
  X-RateLimit-Reset: 0
```

**Metrics**
```
GET /metrics   →  Prometheus-format metrics, labeled by client_key and decision
```

## Running it locally

Requires Python 3.11+, Redis, and (recommended) WSL2 if on Windows.

```bash
git clone https://github.com/meryem-cmd/rate-limiter.git
cd rate-limiter

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # then fill in your Redis connection details

python manage.py migrate

gunicorn --bind 0.0.0.0:8000 --workers 4 --threads 25 -c gunicorn_config.py ratelimiter_service.wsgi:application
```

Configure a client and test it:
```bash
curl -X POST http://127.0.0.1:8000/admin-api/clients/my-client/config \
  -H "Content-Type: application/json" \
  -d '{"requests_per_second": 5, "burst_size": 10, "mode": "token_bucket"}'

curl -i http://127.0.0.1:8000/check/my-client
```

### Optional: Prometheus + Grafana dashboard

```bash
# Prometheus
wget https://github.com/prometheus/prometheus/releases/download/v2.55.1/prometheus-2.55.1.linux-amd64.tar.gz
tar xvfz prometheus-2.55.1.linux-amd64.tar.gz
cd prometheus-2.55.1.linux-amd64
# create prometheus.yml scraping localhost:8000/metrics every 5s, then:
./prometheus --config.file=prometheus.yml
```

Install Grafana, point it at `http://localhost:9090` as a Prometheus data source, and graph:
```promql
sum by (client_key, decision) (rate(rate_limit_requests_total[1m]))
```

**Note:** for Prometheus metrics to work correctly under gunicorn's multi-process model, set `PROMETHEUS_MULTIPROC_DIR` (see `.env.example` / `gunicorn_config.py`) before starting gunicorn — this is required because `prometheus-client`'s default in-process counters don't aggregate across separate worker processes on their own.

## Results

### The core lesson: fixing a race condition

The service was deliberately built with two implementations of the token bucket check — a naive one (separate Redis GET/SET calls) and an atomic one (a single Redis Lua script) — to demonstrate the difference under real concurrent load.

| Metric | Naive (buggy) | Atomic (fixed) |
|---|---|---|
| Requests allowed (limit: ~35) | **80** | **33** |
| Requests denied | 0 | 427 |
| Avg response time | 1.07s–4.47s | 164ms |

The naive version — using separate read/compute/write Redis calls with no atomicity — allowed more than double its configured limit under 15 concurrent virtual users, and never once correctly denied a request. Replacing it with a single atomic Lua script closed the race condition entirely: the allow count landed within expected variance of the true limit, and response latency dropped roughly 10–25x as a side effect of collapsing four network round-trips into one.

### Load testing and diagnosing real infrastructure bottlenecks

Working toward the throughput requirement surfaced five distinct, real bottlenecks, each diagnosed and fixed in turn:

1. **Redis network latency** (hosted free-tier Redis, ~140ms/round-trip) — fixed by running Redis locally
2. **WSGI server connection limits** (Waitress's default cap of 100 simultaneous connections) — fixed with explicit config
3. **A database query on every single rate-limit check** — fixed with a 30-second in-memory cache on client config lookups (throughput ~236 → ~430 req/s)
4. **Redis client connection pool size** — raised from a hardcoded 25 to 500
5. **Python's GIL under a high-thread, single-process server** — latency actually *worsened* with more concurrent threads in one process; switching to multiple gunicorn worker processes (each with its own interpreter and GIL) dropped median latency from 320ms to 43ms (~7x)

Sustained throughput in this local development environment topped out around 230–430 req/s (varying by configuration), short of the 500 req/s target — traced to a genuine, disclosed limitation: the load generator, application server, and Redis instance were all sharing one machine's CPU cores during testing, a well-known confound in local load testing (production benchmarking typically runs the load generator on separate hardware). No correctness issues were found at any tested load — every slowdown traced back to infrastructure, not the rate-limiting logic itself.

## Known limitations / what a production deployment would need

This project prioritizes demonstrating rate-limiting concepts (atomicity, concurrency, algorithm design) over production hardening. A real deployment would additionally need:

- Authentication on the admin config endpoint (currently open)
- A production `SECRET_KEY` and `DEBUG=False`
- Postgres instead of SQLite for the config store, at scale
- TLS termination (nginx/load balancer in front of gunicorn)
- A managed, low-latency Redis instance rather than local/free-tier

## What this project demonstrates

Concurrency control and atomic operations (Redis Lua scripting), algorithm design (token bucket vs. sliding window), API contract design (standard rate-limit headers), systems debugging (diagnosing a chain of real infrastructure bottlenecks under load), and load testing methodology (k6, before/after evidence-based comparisons).
<img width="721" height="326" alt="image" src="https://github.com/user-attachments/assets/da97f02c-70ac-4bb9-ace8-d838e930b505" />
<img width="740" height="338" alt="image" src="https://github.com/user-attachments/assets/a27e4992-968c-4277-912b-57d27b4d6063" />

