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







## Atomic (Lua script) token bucket — fix confirmation

**Config:** requests_per_second=5, burst_size=10 (same client, same config)
**Load:** 15 concurrent VUs, 5 second duration (identical to naive test)
**Expected ceiling:** ~35 ALLOWs

**Actual results:**
- allow_count: 33
- deny_count: 427
- avg response time: 164ms (down from 1.07s-4.47s in the naive version)
- total requests processed: 460 (up from ~85-107 in the naive version)

**Comparison table:**

| Metric              | Naive (buggy) | Atomic (fixed) |
|---------------------|---------------|-----------------|
| allow_count         | 80            | 33              |
| deny_count          | 0             | 427             |
| Over-allowance      | 2.3x the limit| ~0 (within expected variance) |
| Avg response time   | 1.07s-4.47s   | 164ms           |
| Requests in 5s window | ~85-107     | 460             |

**Conclusion:** Replacing the naive 4-call read-compute-write sequence with a
single atomic Redis Lua script eliminated the race condition entirely —
allow_count landed within expected variance of the true ceiling (~35),
and the bucket correctly denied excess requests instead of silently
over-allowing them. As a side benefit, collapsing 4 network round-trips
into 1 also cut response latency roughly 10-25x and more than 4x'd
achievable throughput in the same time window — atomicity and performance
improved together here, not as a tradeoff.









## Sliding window mode — per-client algorithm selection

**Config:** requests_per_second=5, mode=sliding_window (test-sliding client)
**Load:** 20 concurrent threads fired simultaneously

**Actual results:**
- ALLOW: 5
- DENY: 15
- Over-allowance: none

**Conclusion:** The sliding window mode, implemented as an atomic Lua
script over a Redis sorted set, correctly enforced the requests_per_second
limit under real concurrency with zero over-allowance. Confirmed the
per-client mode dispatch (config.mode) correctly ignores burst_size for
sliding window clients and routes to the right algorithm — the same
/check endpoint serves both algorithms transparently based on how each
client was configured.










## Persistence — surviving a service restart

**Test:** Waitress/Django process stopped and restarted three separate
times (visible as three "Serving on..." log lines), with bucket/window
state checked before and after each restart.

**Actual results:**
- test-sliding continued returning 429 DENY consistently across all
  three restarts (state never reset to fresh)
- test-client-2's X-RateLimit-Remaining stayed stable at 9 across
  repeated checks after restart (not jumping back to 10)

**Conclusion:** Rate-limit state survives service restarts because it
lives entirely in Redis, not in the Django/Waitress process's memory.
The service itself is stateless with respect to rate-limiting data —
it can be killed, redeployed, or restarted without resetting any
client's limits. (Redis Cloud's own server-level persistence — AOF/RDB
snapshotting — is managed by the hosted service itself and wasn't
independently tested, since the free tier doesn't expose direct control
over the Redis server process.)