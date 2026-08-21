import requests

r = requests.get("http://127.0.0.1:8000/check/test-client-2")
print("Status:", r.status_code)
print("Remaining:", r.headers.get("X-RateLimit-Remaining"))