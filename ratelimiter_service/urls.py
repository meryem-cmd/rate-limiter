"""
URL configuration for ratelimiter_service project.
"""
from django.contrib import admin
from django.urls import path, include
from limiter.views import CheckRateLimitView, CheckRateLimitNaiveView, metrics_view

urlpatterns = [
    path("admin/", admin.site.urls),
    path("admin-api/", include("limiter.urls")),
    path("check/<str:client_key>", CheckRateLimitView.as_view(), name="check-rate-limit"),
    path("check-naive/<str:client_key>", CheckRateLimitNaiveView.as_view(), name="check-rate-limit-naive"),
    path("metrics", metrics_view, name="metrics"),
]
