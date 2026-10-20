from django.db import models


class StripeEvent(models.Model):
    """Processed webhook events, so a redelivered event is handled only once."""

    event_id = models.CharField(max_length=255, unique=True)
    type = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
