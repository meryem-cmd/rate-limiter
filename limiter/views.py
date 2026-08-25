from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from prometheus_client import multiprocess, CollectorRegistry

from .models import ClientConfig
from .serializers import ClientConfigSerializer
from .token_bucket import (
    check_token_bucket_naive,
    check_token_bucket_atomic,
    check_sliding_window_atomic,
)

CONFIG_CACHE_TTL = 30  # seconds


def get_client_config_cached(client_key):
    cache_key = f"config:{client_key}"
    config = cache.get(cache_key)
    if config is None:
        config = get_object_or_404(ClientConfig, client_key=client_key)
        cache.set(cache_key, config, CONFIG_CACHE_TTL)
    return config


@method_decorator(csrf_exempt, name='dispatch')
class ClientConfigView(APIView):
    """
    POST /admin-api/clients/<client_key>/config  -> create or update config
    GET  /admin-api/clients/<client_key>/config  -> read config
    """

    def get(self, request, client_key):
        config = get_object_or_404(ClientConfig, client_key=client_key)
        serializer = ClientConfigSerializer(config)
        return Response(serializer.data)

    def post(self, request, client_key):
        data = request.data.copy()
        data["client_key"] = client_key

        try:
            config = ClientConfig.objects.get(client_key=client_key)
            created = False
        except ClientConfig.DoesNotExist:
            config = None
            created = True

        serializer = ClientConfigSerializer(config, data=data)

        if serializer.is_valid():
            serializer.save()
            cache.delete(f"config:{client_key}")
            http_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
            return Response(serializer.data, status=http_status)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CheckRateLimitView(APIView):
    """
    GET /check/<client_key>  -> ALLOW/DENY, algorithm depends on the
    client's configured mode (token_bucket or sliding_window)
    """

    def get(self, request, client_key):
        config = get_client_config_cached(client_key)

        if config.mode == ClientConfig.SLIDING_WINDOW:
            result = check_sliding_window_atomic(
                client_key=config.client_key,
                requests_per_second=config.requests_per_second,
            )
        else:
            result = check_token_bucket_atomic(
                client_key=config.client_key,
                requests_per_second=config.requests_per_second,
                burst_size=config.burst_size,
            )

        response_data = {"decision": "ALLOW" if result["allowed"] else "DENY"}
        response = Response(
            response_data,
            status=status.HTTP_200_OK if result["allowed"] else status.HTTP_429_TOO_MANY_REQUESTS,
        )
        response["X-RateLimit-Limit"] = str(result["limit"])
        response["X-RateLimit-Remaining"] = str(max(0, result["remaining"]))
        response["X-RateLimit-Reset"] = str(result["reset_in_seconds"])
        return response


class CheckRateLimitNaiveView(APIView):
    """
    GET /check-naive/<client_key>  -> ALLOW/DENY using the NAIVE (buggy)
    token bucket. Kept intentionally for before/after comparison.
    """

    def get(self, request, client_key):
        config = get_client_config_cached(client_key)

        result = check_token_bucket_naive(
            client_key=config.client_key,
            requests_per_second=config.requests_per_second,
            burst_size=config.burst_size,
        )

        response_data = {"decision": "ALLOW" if result["allowed"] else "DENY"}
        response = Response(
            response_data,
            status=status.HTTP_200_OK if result["allowed"] else status.HTTP_429_TOO_MANY_REQUESTS,
        )
        response["X-RateLimit-Limit"] = str(result["limit"])
        response["X-RateLimit-Remaining"] = str(max(0, result["remaining"]))
        response["X-RateLimit-Reset"] = str(result["reset_in_seconds"])
        return response


def metrics_view(request):
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
    return HttpResponse(generate_latest(registry), content_type=CONTENT_TYPE_LATEST)
