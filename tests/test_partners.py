"""Tours for other providers (Fremdtouren), partner links, colours/visibility in calendars and the iCal free-event option."""

from unittest import mock

import pytest
from django.urls import reverse

from bookings import availability, calsync, pagetitle
from bookings.models import ExternalTour, Partner, PartnerPeriod

from .conftest import in_days
from .test_calsync import WINDOW, ical
from .test_views import aged_token


@pytest.fixture
def partner(guide):
    p = Partner.objects.create(guide=guide, name="Bergschule B+T", email="info@bt.example.ch")
    PartnerPeriod.objects.create(partner=p, start_date=in_days(20), end_date=in_days(30))
    return p


def offer(client, partner, start, end=None, **extra):
    data = {"title": "Hochtour Piz Palü", "url": "https://bt.example.ch/touren/palue", "start_date": start.isoformat(),
            "end_date": (end or start).isoformat(), "contact_name": "Lea Muster", "contact_email": "lea@bt.example.ch",
            "note": "", "website": "", "ts": aged_token(), **extra}
    return client.post(reverse("widget:partner", args=[partner.token]), data)


# --- iCal: events marked "free" ---


FREE_EVENT = "DTSTART;VALUE=DATE:20260310\r\nDTEND;VALUE=DATE:20260311\r\nTRANSP:TRANSPARENT"


def test_free_events_ignored_by_default():
    assert calsync.busy_ranges(ical(FREE_EVENT), *WINDOW) == []


def test_free_events_block_when_option_on():
    assert calsync.busy_ranges(ical(FREE_EVENT), *WINDOW, include_free=True) != []


def test_sync_uses_guide_option(guide):
    guide.ical_url = "https://calendar.example.com/x.ics"
    guide.ical_block_all = True
    guide.save()
    event = f"DTSTART;VALUE=DATE:{in_days(5):%Y%m%d}\r\nTRANSP:TRANSPARENT"
    with mock.patch.object(calsync, "fetch_ical", return_value=ical(event)):
        assert calsync.sync_guide(guide) == 1


# --- Page title ---


def test_page_title_prefers_og_title():
    page = (b'<html><head><title>Startseite | B+T</title><meta property="og:title" content="Piz Pal&uuml; Hochtour">'
            b'<meta property="og:site_name" content="Bergschule B+T"></head></html>')
    info = pagetitle.parse(page)
    assert (info.title, info.provider) == ("Piz Palü Hochtour", "Bergschule B+T")


def test_page_title_falls_back_to_title_tag():
    info = pagetitle.parse(b"<title>\n  Biancograt  </title>")
    assert (info.title, info.provider) == ("Biancograt", "")


def test_provider_from_url():
    assert pagetitle.provider_from_url("https://www.bt.example.ch/x") == "bt.example.ch"


# --- Guide enters a tour for another provider ---


def test_guide_adds_external_tour_with_title_from_link(client, guide):
    client.force_login(guide)
    with mock.patch.object(pagetitle, "fetch", return_value=pagetitle.PageInfo("Piz Palü", "Bergschule B+T")):
        resp = client.post(reverse("dashboard:external_tours"), {
            "ext-url": "https://bt.example.ch/palue", "ext-title": "", "ext-provider": "",
            "ext-start_date": in_days(10).isoformat(), "ext-end_date": in_days(11).isoformat(), "ext-note": "",
        })
    assert resp.status_code == 302
    tour = ExternalTour.objects.get()
    assert (tour.title, tour.provider, tour.status) == ("Piz Palü", "Bergschule B+T", ExternalTour.Status.CONFIRMED)
    states = availability.day_states(guide, in_days(10), in_days(11))
    assert set(states.values()) == {availability.EXTERNAL}


def test_guide_must_type_title_when_page_unreadable(client, guide):
    client.force_login(guide)
    with mock.patch.object(pagetitle, "fetch", side_effect=calsync.CalendarSyncError("x")):
        resp = client.post(reverse("dashboard:external_tours"), {
            "ext-url": "https://bt.example.ch/palue", "ext-title": "", "ext-provider": "",
            "ext-start_date": in_days(10).isoformat(), "ext-end_date": in_days(10).isoformat(),
        })
    assert resp.status_code == 200
    assert not ExternalTour.objects.exists()


def test_external_tour_rejects_non_https(client, guide):
    client.force_login(guide)
    client.post(reverse("dashboard:external_tours"), {
        "ext-url": "javascript:alert(1)", "ext-title": "x", "ext-start_date": in_days(10).isoformat(),
        "ext-end_date": in_days(10).isoformat(),
    })
    assert not ExternalTour.objects.exists()


def test_external_tour_shows_in_calendar(client, guide):
    ExternalTour.objects.create(guide=guide, title="Biancograt", provider="B+T", start_date=in_days(3),
                                end_date=in_days(3))
    client.force_login(guide)
    resp = client.get(reverse("dashboard:calendar") + f"?monat={in_days(3):%Y-%m}")
    assert "Biancograt" in resp.content.decode()


def test_removed_external_tour_frees_days(client, guide):
    tour = ExternalTour.objects.create(guide=guide, title="x", start_date=in_days(3), end_date=in_days(3))
    client.force_login(guide)
    client.post(reverse("dashboard:external_tour_action", args=[tour.pk, "loeschen"]))
    tour.refresh_from_db()
    assert tour.status == ExternalTour.Status.CANCELLED
    assert availability.day_states(guide, in_days(3), in_days(3))[in_days(3)] != availability.EXTERNAL


# --- Partner link ---


def test_partner_page_shows_only_released_periods(client, partner):
    resp = client.get(reverse("widget:partner", args=[partner.token]))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert f"?von={in_days(25):%Y-%m-%d}" in body
    assert f"?von={in_days(35):%Y-%m-%d}" not in body


def test_unknown_partner_token_is_404(client, db):
    assert client.get(reverse("widget:partner", args=["nope"])).status_code == 404


def test_partner_offer_holds_days_and_emails_guide(client, partner, mailoutbox, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        resp = offer(client, partner, in_days(22), in_days(23))
    assert resp.status_code == 200
    tour = ExternalTour.objects.get()
    assert tour.status == ExternalTour.Status.PENDING and tour.partner == partner
    assert availability.day_states(partner.guide, in_days(22), in_days(22))[in_days(22)] == availability.REQUESTED
    assert mailoutbox[-1].to == [partner.guide.email]
    assert "B+T" in mailoutbox[-1].subject


def test_partner_cannot_offer_outside_released_period(client, partner):
    resp = offer(client, partner, in_days(29), in_days(31))
    assert resp.status_code == 400
    assert not ExternalTour.objects.exists()


def test_partner_cannot_offer_taken_days(client, partner, make_booking):
    make_booking(partner.guide, in_days(24))
    assert offer(client, partner, in_days(24)).status_code == 400
    assert not ExternalTour.objects.exists()


def test_partner_period_is_bookable_for_guests_too(guide, partner):
    from bookings.models import OpenPeriod

    OpenPeriod.objects.create(guide=guide, start_date=in_days(60), end_date=in_days(61))  # guide uses open periods
    assert availability.day_states(guide, in_days(25), in_days(25))[in_days(25)] in (availability.FREE,
                                                                                    availability.UNAVAILABLE)
    assert availability.day_states(guide, in_days(40), in_days(40))[in_days(40)] == availability.UNAVAILABLE


def test_guide_accepts_offer_and_partner_is_told(client, partner, mailoutbox, django_capture_on_commit_callbacks):
    offer(client, partner, in_days(22))
    tour = ExternalTour.objects.get()
    client.force_login(partner.guide)
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("dashboard:external_tour_action", args=[tour.pk, "annehmen"]))
    tour.refresh_from_db()
    assert tour.status == ExternalTour.Status.CONFIRMED
    assert mailoutbox[-1].to == ["lea@bt.example.ch"]
    assert availability.day_states(partner.guide, in_days(22), in_days(22))[in_days(22)] == availability.EXTERNAL


def test_guide_declines_offer_frees_days(client, partner):
    offer(client, partner, in_days(22))
    tour = ExternalTour.objects.get()
    client.force_login(partner.guide)
    client.post(reverse("dashboard:external_tour_action", args=[tour.pk, "ablehnen"]))
    tour.refresh_from_db()
    assert tour.status == ExternalTour.Status.DECLINED
    assert availability.day_states(partner.guide, in_days(22), in_days(22))[in_days(22)] != availability.REQUESTED


def test_other_guide_cannot_answer_offer(client, partner, other_guide):
    offer(client, partner, in_days(22))
    tour = ExternalTour.objects.get()
    client.force_login(other_guide)
    assert client.post(reverse("dashboard:external_tour_action", args=[tour.pk, "annehmen"])).status_code == 404


def test_guide_creates_partner_and_period(client, guide):
    client.force_login(guide)
    client.post(reverse("dashboard:availability"), {"form": "partner", "partner-name": "B+T", "partner-email": ""})
    p = Partner.objects.get()
    client.post(reverse("dashboard:availability"), {
        "form": "partner_period", "pp-partner": p.pk, "pp-start_date": in_days(5).isoformat(),
        "pp-end_date": in_days(9).isoformat(), "pp-note": "",
    })
    assert p.periods.count() == 1
    assert p.public_url in client.get(reverse("dashboard:availability")).content.decode()


def test_guide_cannot_release_for_foreign_partner(client, guide, other_guide):
    foreign = Partner.objects.create(guide=other_guide, name="X")
    client.force_login(guide)
    client.post(reverse("dashboard:availability"), {
        "form": "partner_period", "pp-partner": foreign.pk, "pp-start_date": in_days(5).isoformat(),
        "pp-end_date": in_days(9).isoformat(),
    })
    assert not PartnerPeriod.objects.exists()


# --- Scheduled dates visible in the guest calendar ---


def test_widget_calendar_links_departure_days(client, guide, make_departure):
    dep = make_departure(guide)
    resp = client.get(reverse("widget:show", args=[guide.slug]) + f"?monat={dep.start_date:%Y-%m}")
    assert reverse("widget:departure", args=[dep.public_id]) in resp.content.decode()
    assert 'class="day tile span-' in resp.content.decode()
