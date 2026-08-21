# Token Bucket Rate Limiter — Project Notes

A standalone API rate-limiting service built with Django + Redis. This document tracks what's been built, why, and the concepts each step teaches — meant to be read back later for understanding or interview prep.

---

## 1. What this project is

A service other APIs call to decide: should this client's request be **ALLOWED** or **DENIED**, based on a configurable rate limit? This is the same category of problem solved by API gateways at companies like Stripe, GitHub, Cloudflare.

**Core requirements:**
1. Endpoint that returns ALLOW/DENY using a token-bucket algorithm
2. Per-client configurable limits (requests/sec, burst size) via an admin endpoint
3. Persistent state — survives service restarts
4. Correct behavior under concurrent requests (no race conditions)
5. A second, alternate algorithm mode, selectable per client
6. Standard rate-limit response headers
7. Load test proving 500+ concurrent requests/sec

**Stretch goals:** distributed mode (multiple service instances sharing state), a live dashboard.

**What it's meant to teach:** concurrency control, atomic operations, algorithm design, API contracts, load testing.

---

## 2. Tech stack (and why each piece was chosen)

| Piece | Choice | Why |
|---|---|---|
| Backend framework | Django + Django REST Framework | Already known — lets the focus stay on rate-limiting concepts instead of learning new framework syntax |
| Live bucket state | Redis (Redis Cloud, hosted) | Needs atomic read-modify-write operations and fast in-memory access; Redis's Lua scripting solves the concurrency requirement directly |
| Durable config | Django ORM + SQLite | Stores per-client rules (rate, burst, mode) — separate from Redis, which only holds live counters |
| Local environment | Windows, no Docker | Limited disk space ruled out Docker Desktop; used Redis Cloud's free hosted tier instead of a local Redis install |
| WSGI server | Waitress | `manage.py runserver` (Django's dev server) can't handle real concurrent load — it's single-process and not meant for testing. Waitress is a production-grade, pure-Python WSGI server that works natively on Windows (gunicorn does not, since it needs `fork`) |
| Load testing | k6 | Scriptable concurrent load generation with custom metrics (e.g. counting ALLOW vs DENY responses directly) |

**Why Redis holds live state but not config:** Redis is fast and supports atomic operations, which is exactly what's needed for "is there a token available right now, and can I safely decrement it." But it's not the right place for durable configuration data that's rarely written and needs relational structure — that's what Postgres/SQLite + Django ORM is for. This is a common real-world pattern: hot/ephemeral state in Redis, durable config/business data in a relational DB.

---

## 3. Project structure so far

```
D:\rate-limiter\
├── .env                        # Redis Cloud credentials (never committed)
├── .gitignore
├── manage.py
├── reset_bucket.py             # utility script to clear a client's bucket state
├── redis_latency_test.py       # utility script to measure raw Redis round-trip time
├── loadtest_naive.js           # k6 load test script
├── RESULTS.md                  # running log of load test results (the "evidence" doc)
├── ratelimiter_service/        # Django project
│   ├── settings.py             # loads .env, configures REDIS_HOST/PORT/PASSWORD
│   ├── urls.py                 # root URL routing
│   └── wsgi.py
└── limiter/                    # Django app — all rate-limiter logic lives here
    ├── models.py                # ClientConfig model
    ├── serializers.py           # ClientConfigSerializer
    ├── views.py                 # ClientConfigView, CheckRateLimitView
    ├── urls.py                  # app-level URL routing
    └── token_bucket.py          # token bucket algorithm logic
```

---

## 4. What's been built, step by step

### Step 1 — Redis setup (Redis Cloud)
Used Redis Cloud's free hosted tier instead of a local install (Docker Desktop needed more disk space than was available on Windows). Verified connectivity with a standalone Python script using `redis-py` before touching Django at all — isolating "is Redis reachable" from "is Django configured right."

**Lesson:** always verify infrastructure pieces independently before wiring them together. If something breaks later, you know which layer to suspect.

### Step 2 — Django project scaffolding
- `django-admin startproject ratelimiter_service .`
- `python manage.py startapp limiter`
- Registered `rest_framework` and `limiter` in `INSTALLED_APPS`
- Loaded Redis credentials from a `.env` file via `python-dotenv`, kept out of version control via `.gitignore`

**Lesson:** never hardcode credentials in source. Config via environment variables is standard practice.

### Step 3 — `ClientConfig` model (durable config store)
```python
class ClientConfig(models.Model):
    client_key = models.CharField(max_length=255, unique=True)
    requests_per_second = models.FloatField()
    burst_size = models.IntegerField()
    mode = models.CharField(max_length=20, choices=MODE_CHOICES, default=TOKEN_BUCKET)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```
This is the "rules" for each client — separate from Redis, which will hold the live, constantly-changing counters.

### Step 4 — Admin config endpoint
`POST /admin-api/clients/<client_key>/config` — create or update a client's config
`GET /admin-api/clients/<client_key>/config` — read it back

Used `try/except ClientConfig.DoesNotExist` instead of Django's `get_or_create` shortcut — deliberately, because `get_or_create` creates a half-empty row *before* validation runs, which could leave junk data in the DB if validation then failed. The manual version only saves after the serializer confirms the data is valid.

**Lesson:** convenience shortcuts (`get_or_create`) can hide subtle correctness issues. Worth understanding what they do under the hood before using them.

### Step 5 — Naive token bucket + `/check` endpoint (intentionally broken)
`GET /check/<client_key>` — returns ALLOW (200) or DENY (429), with headers:
- `X-RateLimit-Limit`
- `X-RateLimit-Remaining`
- `X-RateLimit-Reset`

**Token bucket algorithm, conceptually:**
- Each client has a bucket holding up to `burst_size` tokens
- Tokens refill continuously at `requests_per_second` per second
- Each request costs 1 token: available → ALLOW + consume; empty → DENY

**The naive implementation, deliberately non-atomic:**
```python
tokens = redis_client.get(bucket_key)          # READ
last_refill = redis_client.get(timestamp_key)   # READ
# ...compute new token count...
redis_client.set(bucket_key, tokens)            # WRITE (separate call)
redis_client.set(timestamp_key, now)            # WRITE (separate call)
```
Four separate Redis round-trips, with no locking between them — built this way on purpose, to demonstrate the race condition with real evidence before fixing it.

### Step 6 — Load testing exposed multiple real issues, in this order

**Issue 1: Django's dev server (`runserver`) couldn't handle concurrent load.**
It's single-process and not meant for this. Under 20 concurrent virtual users (VUs), most requests timed out (up to 9s response times, 76% failure rate) — this wasn't the race condition, it was the dev server falling over.
**Fix:** switched to **Waitress**, a production-grade WSGI server that works natively on Windows (gunicorn requires `fork`, unavailable on Windows) and supports real multi-threading (`--threads=100`).

**Issue 2: Redis Cloud network latency was very high (325ms per round-trip).**
Measured directly with a standalone script timing raw `GET` calls — confirmed the bottleneck was network distance to the Redis Cloud region, not application code. Switching to a Europe-based region cut it to ~140ms (still high for Redis — free-tier shared proxy overhead — but workable for now).
**Lesson:** always measure component latency directly (isolate Redis timing from Django timing) rather than guessing where slowness comes from. This also foreshadows that hitting 500+ req/s later (Step 11) will likely require lower-latency Redis — possibly a local instance via WSL2.

**Issue 3 (the actual target): the race condition, once the above noise was eliminated.**
With a clean test (15 VUs, 5s duration, Waitress running, `http_req_failed` under 6%):

- **Config:** `requests_per_second=5, burst_size=10`
- **Expected ceiling:** ~35 ALLOWs (10 burst + 5×5 refill over 5 seconds)
- **Actual result:** `allow_count=80`, `deny_count=0`

The bucket allowed **more than double** its true capacity and never once denied a request. This confirms a classic **check-then-act race condition**: concurrent requests read the same "before" token count (because an earlier request's WRITE hadn't landed yet when a later request's READ happened), both computed "yes, allowed," and both wrote back results that ignored each other's changes — silently losing decrements.

**This is the central lesson of the whole project**, and now there's real load-test evidence of it, not just a theoretical explanation.

---

## 5. Key concepts learned so far (for interview prep)

- **Race condition / check-then-act bug:** when a read-then-write sequence isn't atomic, concurrent operations can interleave and both act on stale data. This is the single most important concept in this project.
- **Atomicity:** an operation that either completes entirely or has no effect at all, with no observable in-between state. Redis's Lua scripting (coming in Step 7) achieves this by running multiple commands as one uninterruptible unit.
- **Dev server vs. production WSGI server:** Django's built-in `runserver` is single-process and unsuitable for concurrency testing; production servers (Waitress, gunicorn) support real multi-threading/multi-processing.
- **Isolating variables when debugging performance:** measured raw Redis latency separately from full request latency to figure out *which layer* was slow, instead of guessing.
- **Separation of hot state vs. durable config:** Redis for fast-changing live counters, a relational DB for stable per-client configuration — a common real-world architecture pattern.
- **Standard API contract conventions:** `X-RateLimit-Limit` / `X-RateLimit-Remaining` / `X-RateLimit-Reset` headers match the convention used by GitHub, Stripe, Twitter/X, etc.

---

### Step 7 — Fixed the race condition with an atomic Redis Lua script

**The core idea:** Redis executes a Lua script as one indivisible operation — no other command from any other client can run in between its internal steps. This closes the race condition entirely, because the read-compute-write sequence that was previously 4 separate round-trips (each an opportunity for another request to interleave) becomes 1 atomic round-trip.

```python
TOKEN_BUCKET_LUA = """
local tokens_key = KEYS[1]
local timestamp_key = KEYS[2]
local now = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local burst = tonumber(ARGV[3])

local tokens = tonumber(redis.call("GET", tokens_key))
local last_refill = tonumber(redis.call("GET", timestamp_key))

if tokens == nil or last_refill == nil then
    tokens = burst
    last_refill = now
end

local elapsed = now - last_refill
if elapsed < 0 then elapsed = 0 end
local refill = elapsed * rate
tokens = math.min(burst, tokens + refill)

local allowed = 0
if tokens >= 1 then
    allowed = 1
    tokens = tokens - 1
end

redis.call("SET", tokens_key, tostring(tokens))
redis.call("SET", timestamp_key, tostring(now))
return {allowed, tostring(tokens)}
"""
```

Registered once via `redis_client.register_script(...)` — `redis-py` caches the script server-side by SHA1 hash after the first call, so subsequent calls just reference the hash instead of resending the full script text.

Kept **both** the naive and atomic implementations, exposed as separate endpoints (`/check` = atomic, `/check-naive` = buggy, kept intentionally for before/after comparison).

**Verification:** wrote a quick Python script using `threading` to fire 20 truly concurrent requests at the atomic endpoint. Result: **exactly 10 ALLOWs** (matching `burst_size=10`), then clean DENYs — zero over-allowance under real concurrency. First-try correctness.

### Step 8 — Formal load test: atomic vs. naive, side by side

Same k6 config as the naive test (15 VUs, 5s duration) run against the atomic `/check` endpoint, for a fair apples-to-apples comparison.

**Results:**

| Metric | Naive (buggy) | Atomic (fixed) |
|---|---|---|
| allow_count | 80 | 33 |
| deny_count | 0 | 427 |
| Over-allowance | 2.3x the configured limit | ~0 (within expected variance of the ~35 ceiling) |
| Avg response time | 1.07s–4.47s | 164ms |
| Requests processed in 5s window | ~85–107 | 460 |

**Key insight for interviews:** fixing the race condition didn't just fix correctness — it also made the service *faster and higher-throughput*, because collapsing 4 network round-trips into 1 atomic Lua call removed most of the latency, not just the bug. Atomicity and performance improved together here; it wasn't a tradeoff. This is a good example of how the "safe" solution isn't automatically the "slow" one — it depends on where the actual bottleneck is (in this case, network round-trips, not computation).

### Step 9 — Second algorithm: sliding window log

**Design decision:** sliding window mode allows up to `requests_per_second` requests in any rolling 1-second window. No separate burst allowance (unlike token bucket) — `burst_size` is simply unused in this mode. This is a real architectural tradeoff worth being able to explain: token bucket permits controlled bursts (smooths traffic while allowing short spikes), sliding window enforces a harder, more literal cap on requests-per-window (no bursting above the configured rate at all).

**Implementation:** a Redis **sorted set** acts as a request log — each request's timestamp is the score. The atomic Lua script:
1. Drops log entries older than the window (`ZREMRANGEBYSCORE`) — these have "slid out"
2. Counts remaining entries (`ZCARD`)
3. If under the limit, adds the new request (`ZADD`) and allows it; otherwise denies
4. Sets an `EXPIRE` on the key so inactive clients' logs don't linger forever in Redis

Each request gets a unique member ID (`timestamp:uuid4`) rather than just the timestamp — because sorted sets require unique members, and two concurrent requests could theoretically compute the exact same floating-point timestamp, which would silently overwrite rather than add a second entry, undercounting real traffic.

**Mode dispatch:** the *same* `/check/<client_key>` endpoint checks `config.mode` and routes to either `check_token_bucket_atomic` or `check_sliding_window_atomic` — callers don't need to know or care which algorithm is running underneath. This satisfies the "selectable per client" requirement cleanly.

**Verification:** created a client with `requests_per_second=5, mode=sliding_window`, fired 20 concurrent threads. Result: **exactly 5 ALLOW, 15 DENY** — zero over-allowance, and correctly ignored `burst_size` entirely (confirming the dispatch logic routes correctly).

**A debugging story worth remembering:** the first attempt at this test showed 10 ALLOWs instead of the expected 5 — which looked like a bug (as if `burst_size` were being used instead of `requests_per_second`). Turned out to be a **test script bug**, not an application bug — the test's `hit()` function was still hardcoded to the old client's URL (`/check/test-client-2`) instead of the new sliding-window client (`/check/test-sliding`). Lesson: when a result looks surprising, verify the test harness itself before concluding the application is wrong.

### Step 10 — Persistence: state survives a service restart

**What needed proving:** that rate-limit state (live bucket/window counters) survives the Django/Waitress service being killed and restarted — not just the durable `ClientConfig` (which was already safe in SQLite via the Django ORM).

**Test:** restarted the Waitress process three separate times (visible as three distinct `INFO:waitress:Serving on...` log lines) and checked bucket state before/after each restart.

**Results:** a sliding-window client that had already used its 5-request allowance kept returning `429 DENY` consistently across all three restarts — it never reset to "fresh." A token-bucket client's `X-RateLimit-Remaining` stayed stable (`9`) across repeated checks after restart, rather than jumping back to `10`.

**Conclusion:** confirms the service is fully stateless with respect to rate-limit data — all state lives in Redis, not in the Django/Waitress process's memory, so the service can be killed/redeployed/restarted freely without resetting anyone's limits. (Note: this tests *service*-level persistence, which is the practically meaningful case. Redis *server*-level persistence — AOF/RDB snapshotting — was not independently tested, since it was initially on a managed Redis Cloud instance without direct control over the Redis process itself.)

### Step 11 (in progress) — Load testing toward 500+ req/s

This step surfaced a chain of real infrastructure bottlenecks, each diagnosed and fixed in turn — a genuinely valuable debugging sequence for an interview narrative, even though the step isn't fully complete yet.

**Bottleneck 1: Redis Cloud's free-tier latency (revisited from Step 6).**
At ~139ms per round-trip, sustaining 500 req/s was mathematically difficult (500 req/s × ~140ms per request implies needing 70+ requests in flight simultaneously just to keep up, before any other overhead). 
**Fix:** installed Redis locally via **WSL2** (`sudo apt install redis-server`), pointed Django's `.env` at `localhost:6379` (WSL2's automatic localhost-forwarding made this work without extra networking config). Latency dropped from ~139ms to **0.4ms average per GET** — essentially local-network speed.

**Bottleneck 2: Waitress's default connection limit.**
Waitress has a `--connection-limit` flag separate from `--threads`, defaulting to just 100 simultaneous open sockets. Under 500 req/s with multi-hundred-ms response times, far more than 100 connections were open at once, so Waitress started actively refusing new connections (visible in server logs: `WARNING:waitress:total open connections reached the connection limit, no longer accepting new connections`).
**Fix:** explicit flags: `--threads=200 --connection-limit=1000 --channel-timeout=120`.

**Bottleneck 3: A SQLite query on every single rate-limit check.**
`get_object_or_404(ClientConfig, ...)` was hitting the database on every `/check` request to look up that client's config — even though config rarely changes. Under high concurrency, SQLite (not built for heavy concurrent access) likely became a contention point.
**Fix:** added Django's built-in local-memory cache (`django.core.cache`) with a 30-second TTL around the config lookup, removing the DB round-trip from the hot path for repeat requests to the same client.
**Result:** throughput improved from ~236 req/s to ~430 req/s, with `http_req_failed` dropping to near 0%.

**Bottleneck 4 (fix applied, not yet cleanly verified): Redis connection pool size.**
Found `max_connections=25` hardcoded on the `redis.Redis(...)` client — meaning only 25 requests could talk to Redis simultaneously regardless of how many Waitress threads or k6 VUs were available, creating a queuing bottleneck.
**Fix applied:** raised to `max_connections=500`. Also raised k6's `preAllocatedVUs`/`maxVUs` since earlier runs hit k6's own VU ceiling (`WARN: Insufficient VUs, reached 300 active VUs and cannot initialize more`).

**Status at end of today's session:** the fix for bottleneck 4 was applied, but the verification run was interrupted (manually stopped) before completing its full 15-second duration, so the result isn't trustworthy yet — needs a clean, uninterrupted re-run. The best *clean* (fully completed) result so far is **~430 req/s** with near-zero failures, from before the connection-pool fix was even applied — so the true number after all fixes is likely higher, but unconfirmed.

**Lesson worth remembering:** this step is a good example of load testing revealing a *stack* of independent bottlenecks, each masking the next — Redis network latency, then a WSGI server connection cap, then a database contention point, then a client-side connection pool limit. Fixing one exposed the next rather than solving everything at once. This is realistic: production performance problems are rarely a single root cause.

**Also worth remembering:** an interrupted k6 run produces misleading numbers (inflated failure rates, artificially short durations) — always let load tests run to completion before drawing conclusions from them.

---

## 6. What's next (not yet built)

- **Step 11 (finish):** Get one clean, uninterrupted 15-second run of the 500 req/s test with all four fixes in place (WSL2 Redis, Waitress connection limit, config caching, Redis connection pool size). Confirm whether 500 req/s is actually sustained, and if not, keep isolating — possibly Waitress's threading model itself, or single-machine resource limits (k6, Waitress, and Redis all running on the same laptop simultaneously).
- **Step 12 (stretch):** Metrics/dashboard (Prometheus + Grafana, or simple polling page).
- **Step 13 (stretch):** Distributed mode — multiple service instances sharing the same Redis.
- **Step 14:** Final write-up combining all before/after results.

---

## 7. RESULTS.md (evidence log, kept alongside this file)

```markdown
# Rate Limiter — Results Log

## Naive (non-atomic) token bucket — race condition demonstration

**Config:** requests_per_second=5, burst_size=10
**Load:** 15 concurrent VUs, 5 second duration
**Expected ceiling:** ~35 ALLOWs (10 burst + 5×5 refill)

**Actual results:**
- allow_count: 80
- deny_count: 0
- http_req_failed: 5.88%

**Conclusion:** The naive read-compute-write implementation, using separate
Redis GET/SET calls with no atomicity, allowed more than double the
configured limit under concurrent load, and never triggered a single DENY.
This confirms a check-then-act race condition: concurrent requests read
stale token counts before earlier writes complete, causing both to be
allowed when only one should have been.

---

## Atomic (Lua script) token bucket — fix confirmation

**Config:** requests_per_second=5, burst_size=10 (same client, same config)
**Load:** 15 concurrent VUs, 5 second duration (identical to naive test)

**Actual results:**
- allow_count: 33
- deny_count: 427
- avg response time: 164ms (down from 1.07s-4.47s)
- total requests processed: 460 (up from ~85-107)

**Conclusion:** Replacing the naive 4-call sequence with a single atomic
Redis Lua script eliminated the race condition — allow_count landed within
expected variance of the true ~35 ceiling, and the bucket correctly denied
excess requests. Collapsing 4 round-trips into 1 also cut latency ~10-25x
and more than 4x'd throughput in the same window — correctness and
performance improved together, not as a tradeoff.

---

## Sliding window mode — per-client algorithm selection

**Config:** requests_per_second=5, mode=sliding_window
**Load:** 20 concurrent threads fired simultaneously

**Actual results:**
- ALLOW: 5
- DENY: 15
- Over-allowance: none

**Conclusion:** The sliding window mode (atomic Lua script over a Redis
sorted set) correctly enforced the limit under real concurrency with zero
over-allowance, and correctly ignored burst_size — confirming per-client
mode dispatch routes to the right algorithm through the same endpoint.

---

## Persistence — surviving a service restart

**Test:** Waitress/Django process stopped and restarted three separate
times, bucket/window state checked before and after each restart.

**Actual results:**
- Sliding-window client continued returning 429 DENY consistently across
  all three restarts (state never reset to fresh)
- Token-bucket client's X-RateLimit-Remaining stayed stable at 9 across
  repeated checks after restart (not jumping back to 10)

**Conclusion:** Rate-limit state survives service restarts because it
lives entirely in Redis, not in the Django/Waitress process's memory —
the service is stateless with respect to rate-limiting data.

---

## Load testing toward 500+ req/s — bottleneck chain (in progress)

**Bottleneck 1 — Redis Cloud latency (~139ms/round-trip):**
Fixed by switching to local Redis via WSL2 → 0.4ms average per GET.

**Bottleneck 2 — Waitress default connection-limit (100):**
Fixed with explicit `--connection-limit=1000 --channel-timeout=120`.

**Bottleneck 3 — SQLite queried on every /check request:**
Fixed with Django local-memory caching (30s TTL) on ClientConfig lookups.
Result: throughput improved from ~236 req/s to ~430 req/s (clean,
completed run), http_req_failed near 0%.

**Bottleneck 4 — Redis client connection pool capped at 25:**
Fix applied (raised to 500) but not yet cleanly verified — last test run
was manually interrupted before completing its full duration, so the
post-fix number is unconfirmed. Best clean (fully completed) result to
date: ~430 req/s, from before this fourth fix.

**Status:** not yet complete. Next session: get one full, uninterrupted
15-second run with all four fixes in place and record the final number.
```