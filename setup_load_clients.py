import requests

BASE = "http://127.0.0.1:8000/admin-api/clients"

for i in range(50):
    client_key = f"load-client-{i}"
    requests.post(f"{BASE}/{client_key}/config", json={
        "requests_per_second": 20,
        "burst_size": 20,
        "mode": "token_bucket",
    })

print("Created 50 load-test clients, each allowed 20 req/s (50 x 20 = 1000 req/s theoretical ceiling)")