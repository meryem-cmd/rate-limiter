import requests
import threading

# PART 1: Create the sliding window client
response = requests.post(
    "http://127.0.0.1:8000/admin-api/clients/test-sliding/config",
    json={
        "requests_per_second": 5,
        "burst_size": 10,
        "mode": "sliding_window"
    }
)
print("Client created:", response.status_code, response.json())


# PART 2: Burst test — now correctly targeting the sliding window client
def hit():
    r = requests.get("http://127.0.0.1:8000/check/test-sliding")
    print(r.status_code, r.json())

threads = [threading.Thread(target=hit) for _ in range(20)]
for t in threads:
    t.start()
for t in threads:
    t.join()


import requests

# Step 1: ek request bhejo aur remaining dekho
r = requests.get("http://127.0.0.1:8000/check/test-client-2")
print("Before restart:")
print("Status:", r.status_code)
print("Remaining:", r.headers.get("X-RateLimit-Remaining"))