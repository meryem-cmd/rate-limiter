# ClientConfig is a Django database model used to store the rate-limiting settings for different clients of your application. Each client gets a unique client_key, a limit for how many requests it can make per second, a burst_size that controls how much sudden traffic it can handle, and a mode that determines whether the system uses Token Bucket or Sliding Window rate limiting. Django also automatically records when the configuration was created and last updated. So basically, this table tells your rate-limiting system "for this client, how much traffic should I allow and which algorithm should I use?"
from django.db import models


class ClientConfig(models.Model):
    TOKEN_BUCKET = "token_bucket"
    SLIDING_WINDOW = "sliding_window"

    MODE_CHOICES = [
        (TOKEN_BUCKET, "Token Bucket"),
        (SLIDING_WINDOW, "Sliding Window"),
    ]

    client_key = models.CharField(max_length=255, unique=True)
    requests_per_second = models.FloatField()
    burst_size = models.IntegerField()
    mode = models.CharField(
        max_length=20,
        choices=MODE_CHOICES,
        default=TOKEN_BUCKET,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.client_key} ({self.mode})"