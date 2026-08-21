import time
import uuid
import redis
from django.conf import settings
from prometheus_client import Counter as PromCounter

redis_client = redis.Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    password=settings.REDIS_PASSWORD,
    decode_responses=True,
    max_connections=200
)

# Prometheus metric — labeled by client and decision, so the dashboard
# can show allow/deny rates broken down per client.
rate_limit_requests_total = PromCounter(
    "rate_limit_requests_total",
    "Total number of rate limit checks, labeled by client and decision",
    labelnames=["client_key", "decision"],
)


def check_token_bucket_naive(client_key, requests_per_second, burst_size):
    """
    NAIVE, NON-ATOMIC implementation. Intentionally has a race condition:
    the read-compute-write sequence below is not a single atomic operation,
    so concurrent requests for the same client_key can interleave and
    both read the same "before" state, leading to over-allowed requests.
    """
    bucket_key = f"bucket:{client_key}:tokens"
    timestamp_key = f"bucket:{client_key}:last_refill"

    now = time.time()

    # --- READ ---
    tokens = redis_client.get(bucket_key)
    last_refill = redis_client.get(timestamp_key)

    if tokens is None or last_refill is None:
        tokens = float(burst_size)
        last_refill = now
    else:
        tokens = float(tokens)
        last_refill = float(last_refill)

    # --- COMPUTE ---
    elapsed = now - last_refill
    refill_amount = elapsed * requests_per_second
    tokens = min(burst_size, tokens + refill_amount)

    if tokens >= 1:
        allowed = True
        tokens -= 1
    else:
        allowed = False

    # --- WRITE (separate calls — this is the race condition window) ---
    redis_client.set(bucket_key, tokens)
    redis_client.set(timestamp_key, now)

    if tokens >= 1:
        reset_in_seconds = 0
    else:
        reset_in_seconds = (1 - tokens) / requests_per_second

    rate_limit_requests_total.labels(
        client_key=client_key,
        decision="allow" if allowed else "deny",
    ).inc()

    return {
        "allowed": allowed,
        "remaining": int(tokens),
        "limit": burst_size,
        "reset_in_seconds": round(reset_in_seconds, 3),
    }


# Lua script: atomic token bucket check-and-consume.
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

_token_bucket_script = redis_client.register_script(TOKEN_BUCKET_LUA)


def check_token_bucket_atomic(client_key, requests_per_second, burst_size):
    """
    ATOMIC implementation using a Redis Lua script.
    """
    bucket_key = f"bucket:{client_key}:tokens"
    timestamp_key = f"bucket:{client_key}:last_refill"

    now = time.time()

    result = _token_bucket_script(
        keys=[bucket_key, timestamp_key],
        args=[now, requests_per_second, burst_size],
    )

    allowed = bool(int(result[0]))
    tokens = float(result[1])

    if tokens >= 1:
        reset_in_seconds = 0
    else:
        reset_in_seconds = (1 - tokens) / requests_per_second

    rate_limit_requests_total.labels(
        client_key=client_key,
        decision="allow" if allowed else "deny",
    ).inc()

    return {
        "allowed": allowed,
        "remaining": int(tokens),
        "limit": burst_size,
        "reset_in_seconds": round(reset_in_seconds, 3),
    }


# Lua script: atomic sliding window log check-and-record.
SLIDING_WINDOW_LUA = """
local log_key = KEYS[1]

local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

redis.call("ZREMRANGEBYSCORE", log_key, "-inf", now - window)

local count = redis.call("ZCARD", log_key)

local allowed = 0
if count < limit then
    redis.call("ZADD", log_key, now, member)
    allowed = 1
    count = count + 1
end

redis.call("EXPIRE", log_key, math.ceil(window) * 2)

return {allowed, count}
"""

_sliding_window_script = redis_client.register_script(SLIDING_WINDOW_LUA)


def check_sliding_window_atomic(client_key, requests_per_second, window_size=1.0):
    """
    ATOMIC sliding window log implementation.
    """
    log_key = f"sliding:{client_key}:log"
    now = time.time()
    limit = requests_per_second

    member = f"{now}:{uuid.uuid4()}"

    result = _sliding_window_script(
        keys=[log_key],
        args=[now, window_size, limit, member],
    )

    allowed = bool(int(result[0]))
    count = int(result[1])
    remaining = max(0, int(limit) - count)

    rate_limit_requests_total.labels(
        client_key=client_key,
        decision="allow" if allowed else "deny",
    ).inc()

    return {
        "allowed": allowed,
        "remaining": remaining,
        "limit": int(limit),
        "reset_in_seconds": round(window_size, 3),
    }
