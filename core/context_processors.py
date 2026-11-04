from django.conf import settings
from django.utils import translation


def site(request):
    """Language switcher and operator details for every page (legal links in the footer, R-001)."""
    return {
        "LANGUAGE_CHOICES": settings.LANGUAGES,
        "CURRENT_LANGUAGE": translation.get_language(),
        "OPERATOR_NAME": settings.OPERATOR_NAME,
    }
