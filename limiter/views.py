from django.http import HttpResponse, JsonResponse
from django.views import View
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from prometheus_client import multiprocess, CollectorRegistry
from .token_bucket import check_token_bucket_atomic, check_token_bucket_naive, check_sliding_window_atomic


class ClientConfigView(View):
    def get(self, request, client_key):
        return JsonResponse({"client_key": client_key, "requests_per_second": 10, "burst_size": 20})


class CheckRateLimitView(View):
    def get(self, request, client_key):
        result = check_token_bucket_atomic(client_key, requests_per_second=10, burst_size=20)
        status = 200 if result["allowed"] else 429
        return JsonResponse({"decision": "ALLOW" if result["allowed"] else "DENY"}, status=status)


class CheckRateLimitNaiveView(View):
    def get(self, request, client_key):
        result = check_token_bucket_naive(client_key, requests_per_second=10, burst_size=20)
        status = 200 if result["allowed"] else 429
        return JsonResponse({"decision": "ALLOW" if result["allowed"] else "DENY"}, status=status)


def metrics_view(request):
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
    return HttpResponse(generate_latest(registry), content_type=CONTENT_TYPE_LATEST)
