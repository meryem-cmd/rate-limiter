from rest_framework import serializers
from .models import ClientConfig


class ClientConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClientConfig
        fields = [
            "client_key",
            "requests_per_second",
            "burst_size",
            "mode",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]