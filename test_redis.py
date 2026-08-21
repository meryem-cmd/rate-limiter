import redis

r = redis.Redis(
    host="flock-fowl-salt-47508.db.redis.io",
    port=16169,
    password="1ZRlvDthwZicEfyMZt9UyCcWgRaSH7Mt",
    decode_responses=True
)

r.set("test_key", "hello from python")
value = r.get("test_key")
print("Got back:", value)