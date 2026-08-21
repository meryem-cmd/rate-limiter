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

r.delete("bucket:test-client-2:tokens")
r.delete("bucket:test-client-2:last_refill")
print("Bucket reset.")