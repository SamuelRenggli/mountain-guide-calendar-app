from django.conf import settings

DEFAULT_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "form-action 'self' https://checkout.stripe.com https://connect.stripe.com; "
    "frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
)


def widget_csp(frame_ancestors: str) -> str:
    return DEFAULT_CSP.replace("frame-ancestors 'none'", f"frame-ancestors {frame_ancestors}")


class ContentSecurityPolicyMiddleware:
    """Adds a strict CSP unless the view already set one (the embeddable widget does)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/" + settings.ADMIN_URL):
            return response  # Django admin uses inline scripts
        response.headers.setdefault("Content-Security-Policy", DEFAULT_CSP)
        return response
