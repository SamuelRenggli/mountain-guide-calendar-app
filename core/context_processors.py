from django.conf import settings
from django.utils import translation


def site(request):
    """Language switcher and operator details for every page (legal links in the footer, R-001)."""
    return {
        "LANGUAGE_CHOICES": settings.LANGUAGES,
        "CURRENT_LANGUAGE": translation.get_language(),
        "OPERATOR_NAME": settings.OPERATOR_NAME,
    }


# Dashboard menu: four sections, each with its own tabs. (label, url name, url names that mark the tab active)
_ = translation.gettext_lazy
NAV = [
    ("tours", _("Meine Touren"), "dashboard:home", [
        (_("Anfragen & Angebote"), "dashboard:home", {"home", "booking", "invoice"}),
        (_("Tour ausschreiben"), "dashboard:tours", {"tours", "tour_new", "tour_edit"}),
        (_("Ausgeschriebene Touren"), "dashboard:departures",
         {"departures", "departure", "departure_new", "departure_edit"}),
    ]),
    ("calendar", _("Kalender"), "dashboard:calendar", [
        (_("Übersicht"), "dashboard:calendar", {"calendar"}),
        (_("Verfügbarkeit"), "dashboard:availability", {"availability"}),
        (_("Externe Anbieter"), "dashboard:external_tours", {"external_tours"}),
    ]),
    ("accounting", _("Buchhaltung"), "dashboard:export", [(_("Buchhaltung"), "dashboard:export", {"export"})]),
    ("settings", _("Einstellungen"), "dashboard:settings", [(_("Einstellungen"), "dashboard:settings", {"settings"})]),
]


def nav(request):
    """Which menu section and tab the current dashboard page belongs to."""
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    match = getattr(request, "resolver_match", None)
    current = match.url_name if match and match.namespace == "dashboard" else None
    sections, tabs = [], []
    for _key, label, url_name, section_tabs in NAV:
        active = any(current in names for _label, _url, names in section_tabs)
        sections.append({"label": label, "url_name": url_name, "active": active})
        if active and len(section_tabs) > 1:
            tabs = [{"label": t, "url_name": u, "active": current in names} for t, u, names in section_tabs]
    return {"nav_sections": sections, "nav_tabs": tabs}
