from django.conf import settings
from django.core.cache import cache


def client_ip(request) -> str:
    """Client IP, trusting X-Forwarded-For only for the configured number of proxies."""
    if settings.NUM_PROXIES:
        forwarded = [ip.strip() for ip in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if ip.strip()]
        if len(forwarded) >= settings.NUM_PROXIES:
            return forwarded[-settings.NUM_PROXIES]
    return request.META.get("REMOTE_ADDR", "")


def allow(key: str, limit: int, window_seconds: int) -> bool:
    """Fixed-window counter. Returns False once `limit` hits happened within the window."""
    cache_key = f"rl:{key}"
    cache.add(cache_key, 0, window_seconds)
    try:
        count = cache.incr(cache_key)
    except ValueError:  # expired between add and incr
        cache.set(cache_key, 1, window_seconds)
        count = 1
    return count <= limit
