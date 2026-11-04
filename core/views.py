from django.conf import settings
from django.http import Http404
from django.shortcuts import render
from django.utils import translation

from .middleware import widget_csp

# R-001: privacy policy, legal notice and terms of use (Tourchef ↔ guide), each in four languages.
LEGAL_PAGES = {"datenschutz": "privacy", "impressum": "imprint", "nutzungsbedingungen": "terms"}


def legal(request, page):
    if page not in LEGAL_PAGES:
        raise Http404
    lang = translation.get_language()
    if lang not in dict(settings.LANGUAGES):
        lang = settings.LANGUAGE_CODE
    response = render(request, f"legal/{LEGAL_PAGES[page]}_{lang}.html", {
        "page": page,
        "operator_name": settings.OPERATOR_NAME,
        "operator_address": settings.OPERATOR_ADDRESS,
        "privacy_email": settings.PRIVACY_EMAIL,
        "site_url": settings.SITE_URL,
    })
    # Linked from the widget, so it may open inside the guide's iframe.
    response.headers["Content-Security-Policy"] = widget_csp("*")
    response.xframe_options_exempt = True
    return response
