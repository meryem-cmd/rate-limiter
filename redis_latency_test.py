import time
import redis
import os
from dotenv import load_dotenv

load_dotenv()

r = redis.Redis(
    host=os.getenv("REDIS_HOST"),
    port=int(os.getenv("REDIS_PORT")),
    password=os.getenv("REDIS_PASSWORD"),
    decode_responses=True,
)

# Warm up connection first (exclude connection setup from timing)
r.ping()

start = time.time()
for _ in range(10):
    r.get("test_key")
elapsed = time.time() - start

print(f"10 GETs took {elapsed:.3f}s total, {elapsed/10*1000:.1f}ms average per GET")