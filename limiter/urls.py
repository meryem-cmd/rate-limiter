from django.urls import path
from .views import ClientConfigView, CheckRateLimitView

urlpatterns = [
    path("clients/<str:client_key>/config", ClientConfigView.as_view(), name="client-config"),
]