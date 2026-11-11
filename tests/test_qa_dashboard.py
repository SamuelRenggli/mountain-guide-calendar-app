"""QA probe tests for the guide dashboard (no app code changed). Failing tests document bugs."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from bookings import departures
from bookings.models import (
    BlockedPeriod, Booking, Departure, Draft, ExternalTour, Message, Notification, Partner, PartnerPeriod, Picture, Tour,
)

from .conftest import at, in_days

S = Booking.Status


@pytest.fixture
def gclient(client, guide):
    client.force_login(guide)
    return client


def reauth(client):
    client.post(reverse("accounts:reauth"), {"password": "sehr-geheimes-pw-123"})


# --- Empty account: every page renders ------------------------------------------------------------

EMPTY_PAGES = ["home", "departures", "departure_new", "tours", "tour_new", "availability", "external_tours",
               "calendar", "settings", "guests", "pictures"]


@pytest.mark.parametrize("name", EMPTY_PAGES)
def test_empty_account_pages_render(gclient, name):
    assert gclient.get(reverse(f"dashboard:{name}")).status_code == 200


def test_empty_export_renders_and_downloads(gclient):
    reauth(gclient)
    assert gclient.get(reverse("dashboard:export")).status_code == 200
    for fmt in ("csv", "xlsx"):
        r = gclient.get(reverse("dashboard:export"), {"start": "2026-01-01", "end": "2026-12-31", "format": fmt})
        assert r.status_code == 200 and r["Content-Disposition"].startswith("attachment")


@pytest.mark.parametrize("params", [
    {"start": "2026-12-31", "end": "2026-01-01", "format": "csv"},
    {"start": "kaputt", "end": "2026-01-01", "format": "csv"},
    {"start": "2026-01-01", "end": "2026-02-01", "format": "pdf"},
    {"start": "0001-01-01", "end": "9999-12-31", "format": "csv"},
])
def test_export_invalid_or_extreme_ranges(gclient, params):
    reauth(gclient)
    assert gclient.get(reverse("dashboard:export"), params).status_code == 200


# --- Calendar overview: month borders and bad query params -------------------------------------------

@pytest.mark.parametrize("monat", ["2026-12", "2027-01", "2026-13", "abc", "2026-1-1", "", "-1-5", "2026-00"])
def test_calendar_month_params(gclient, monat):
    assert gclient.get(reverse("dashboard:calendar"), {"monat": monat}).status_code == 200


def test_calendar_year_wrap_links(gclient):
    html = gclient.get(reverse("dashboard:calendar"), {"monat": "2026-12"}).content.decode()
    assert "monat=2027-01" in html and "monat=2026-11" in html


@pytest.mark.parametrize("monat", ["9999-12", "0001-01"])
def test_calendar_extreme_months_no_500(gclient, monat):
    """BUG: shift_month() builds date(10000, 1, 1) / date(0, 12, 1) -> ValueError -> 500."""
    assert gclient.get(reverse("dashboard:calendar"), {"monat": monat}).status_code == 200


# --- departure_new with bad query params ----------------------------------------------------------

def test_departure_new_bad_template_id_is_404_not_500(gclient):
    """BUG: get_object_or_404(Tour, pk='abc') raises ValueError -> 500."""
    r = gclient.get(reverse("dashboard:departure_new"), {"vorlage": "abc"})
    assert r.status_code in (200, 404)


def test_departure_new_bad_date_param(gclient):
    assert gclient.get(reverse("dashboard:departure_new"), {"datum": "2026-02-30"}).status_code == 200


def test_departure_new_other_guides_template_404(gclient, other_guide):
    t = Tour.objects.create(guide=other_guide, title="X", price_per_person=1, duration_days=1, min_participants=1,
                            max_participants=2)
    assert gclient.get(reverse("dashboard:departure_new"), {"vorlage": t.pk}).status_code == 404


# --- Booking actions in every status --------------------------------------------------------------

ACTIONS = {
    "accept": lambda b: {"accept-start_date": in_days(20).isoformat(), "accept-end_date": in_days(20).isoformat(),
                         "accept-guide_rate": "600", "accept-price": "", "accept-decision_date": in_days(18).isoformat(),
                         "accept-answer_deadline": in_days(5).isoformat()},
    "reject": lambda b: {"reject-message": "nein"},
    "cancel": lambda b: {"cancel-message": "weg", "cancel-sure": "on"},
    "change": lambda b: {"change-start_date": in_days(21).isoformat(), "change-end_date": in_days(21).isoformat(),
                         "change-participants": "3", "change-price": "777"},
    "propose": lambda b: {"propose-start_date": in_days(25).isoformat(), "propose-end_date": in_days(25).isoformat()},
    "refund": lambda b: {"refund-amount": "10"},
    "paid": lambda b: {},
    "ask_details": lambda b: {},
    "unbekannt": lambda b: {},
}


@pytest.mark.parametrize("status", [s for s in S.values])
@pytest.mark.parametrize("action", list(ACTIONS))
def test_booking_action_any_status_no_500(gclient, guide, make_booking, status, action):
    b = make_booking(guide, in_days(10), status=status, price=Decimal("500"))
    r = gclient.post(reverse("dashboard:booking_action", args=[b.pk, action]), ACTIONS[action](b))
    assert r.status_code in (302, 400)


@pytest.mark.parametrize("status", [S.PAID, S.CANCELLED, S.RESCHEDULE_PROPOSED, S.PAYMENT_PENDING, S.EXPIRED])
@pytest.mark.parametrize("action", list(ACTIONS))
def test_registration_action_any_status_no_500(gclient, stripe_guide, make_departure, make_registration, status,
                                               action):
    dep = make_departure(stripe_guide)
    b = make_registration(dep, status=status)
    r = gclient.post(reverse("dashboard:booking_action", args=[b.pk, action]), ACTIONS[action](b))
    assert r.status_code in (302, 400)
    assert gclient.get(reverse("dashboard:booking", args=[b.pk])).status_code == 200


@pytest.mark.parametrize("status", [s for s in S.values])
def test_booking_detail_and_invoice_render_any_status(gclient, guide, make_booking, status):
    b = make_booking(guide, in_days(-3), status=status, price=Decimal("500"))
    assert gclient.get(reverse("dashboard:booking", args=[b.pk])).status_code == 200
    assert gclient.get(reverse("dashboard:invoice", args=[b.pk])).status_code in (200, 302)


def test_invalid_form_inputs_rerender_400(gclient, guide, make_booking):
    b = make_booking(guide, in_days(10))
    for action, data in [("accept", {"accept-start_date": "x"}), ("change", {"change-participants": "999"}),
                         ("propose", {"propose-incurred_costs": "50"}), ("refund", {"refund-amount": "-1"}),
                         ("cancel", {})]:
        r = gclient.post(reverse("dashboard:booking_action", args=[b.pk, action]), data)
        assert r.status_code == 400, action


def test_accept_twice_updates_offer(gclient, guide, make_booking):
    b = make_booking(guide, in_days(10))
    url = reverse("dashboard:booking_action", args=[b.pk, "accept"])
    data = ACTIONS["accept"](b)
    gclient.post(url, data)
    data["accept-price"] = "999"
    gclient.post(url, data)
    b.refresh_from_db()
    assert b.status == S.OFFERED and b.price == Decimal("999")


def test_change_confirmed_booking_onto_blocked_day_is_refused(gclient, guide, make_booking):
    """BUG (suspected): change_booking() skips check_days_free -> confirmed tour can be moved onto a blocked day."""
    b = make_booking(guide, in_days(10), status=S.CONFIRMED, price=Decimal("500"))
    BlockedPeriod.objects.create(guide=guide, start_date=in_days(21), end_date=in_days(21))
    gclient.post(reverse("dashboard:booking_action", args=[b.pk, "change"]), ACTIONS["change"](b))
    b.refresh_from_db()
    assert b.start_date == in_days(10)


def test_change_confirmed_booking_onto_departure_day_is_refused(gclient, stripe_guide, make_booking, make_departure):
    b = make_booking(stripe_guide, in_days(10), status=S.CONFIRMED, price=Decimal("500"))
    make_departure(stripe_guide, start=in_days(21))
    gclient.post(reverse("dashboard:booking_action", args=[b.pk, "change"]), ACTIONS["change"](b))
    b.refresh_from_db()
    assert b.start_date == in_days(10)


def test_change_confirmed_booking_into_past_is_refused(gclient, guide, make_booking):
    b = make_booking(guide, in_days(10), status=S.CONFIRMED, price=Decimal("500"))
    data = ACTIONS["change"](b)
    data["change-start_date"] = data["change-end_date"] = in_days(-30).isoformat()
    gclient.post(reverse("dashboard:booking_action", args=[b.pk, "change"]), data)
    b.refresh_from_db()
    assert b.start_date == in_days(10)


def test_propose_new_date_onto_blocked_day_is_refused(gclient, guide, make_booking):
    """BUG (suspected): propose_new_date() skips check_days_free."""
    b = make_booking(guide, in_days(10), status=S.CONFIRMED, price=Decimal("500"))
    BlockedPeriod.objects.create(guide=guide, start_date=in_days(25), end_date=in_days(25))
    gclient.post(reverse("dashboard:booking_action", args=[b.pk, "propose"]), ACTIONS["propose"](b))
    b.refresh_from_db()
    assert b.status == S.CONFIRMED


def test_ask_details_on_rejected_booking(gclient, guide, make_booking):
    """Asking a rejected/cancelled guest for planning details makes no sense."""
    b = make_booking(guide, in_days(10), status=S.REJECTED)
    gclient.post(reverse("dashboard:booking_action", args=[b.pk, "ask_details"]))
    assert not b.messages.exists()


def test_other_guides_booking_404(gclient, other_guide, make_booking):
    b = make_booking(other_guide, in_days(10))
    assert gclient.get(reverse("dashboard:booking", args=[b.pk])).status_code == 404
    assert gclient.post(reverse("dashboard:booking_action", args=[b.pk, "reject"])).status_code == 404


def test_invoice_future_tour_shows_error(gclient, guide, make_booking):
    b = make_booking(guide, in_days(5), status=S.CONFIRMED, price=Decimal("500"))
    r = gclient.post(reverse("dashboard:invoice", args=[b.pk]), {
        "price": "500", "spesen-TOTAL_FORMS": "0", "spesen-INITIAL_FORMS": "0"})
    assert r.status_code == 200
    b.refresh_from_db()
    assert b.status == S.CONFIRMED


def test_invoice_twice_then_paid(gclient, guide, make_booking):
    b = make_booking(guide, in_days(-2), status=S.CONFIRMED, price=Decimal("500"))
    for initial, price in (("0", "500"), ("1", "450")):  # the correction page renders the bill's expense as initial
        r = gclient.post(reverse("dashboard:invoice", args=[b.pk]), {
            "price": price, "spesen-TOTAL_FORMS": "1", "spesen-INITIAL_FORMS": initial,
            "spesen-0-description": "Hütte", "spesen-0-amount": "80"})
        assert r.status_code == 302
    b.refresh_from_db()
    assert b.status == S.AWAITING_PAYMENT and b.price == Decimal("530.00") and b.invoice_lines.count() == 2
    gclient.post(reverse("dashboard:booking_action", args=[b.pk, "paid"]))
    gclient.post(reverse("dashboard:booking_action", args=[b.pk, "paid"]))
    b.refresh_from_db()
    assert b.status == S.PAID and b.amount_paid == Decimal("530.00")
    assert gclient.get(reverse("dashboard:invoice", args=[b.pk])).status_code == 302


# --- Messages -------------------------------------------------------------------------------------

def test_message_action_paths(gclient, guide, make_booking):
    b = make_booking(guide, in_days(10))
    m = Message.objects.create(booking=b, direction=Message.Direction.FROM_GUEST, body="hi", needs_review=True)
    ok = Message.objects.create(booking=b, direction=Message.Direction.FROM_GUEST, body="ok")
    assert gclient.post(reverse("dashboard:booking_message_action", args=[b.pk, ok.pk, "loeschen"])).status_code == 404
    assert gclient.post(reverse("dashboard:booking_message_action", args=[b.pk, m.pk, "unbekannt"])).status_code == 302
    assert gclient.post(reverse("dashboard:booking_message_action", args=[b.pk, m.pk, "freigeben"])).status_code == 302
    m.refresh_from_db()
    assert not m.needs_review
    assert gclient.post(reverse("dashboard:booking_message_action", args=[b.pk, m.pk, "loeschen"])).status_code == 404
    assert gclient.post(reverse("dashboard:booking_message", args=[b.pk]), {"msg-body": ""}).status_code == 400
    assert gclient.post(reverse("dashboard:booking_message", args=[b.pk]), {"msg-body": "x" * 5001}).status_code == 400


# --- Departures -----------------------------------------------------------------------------------

def test_move_departure_earlier_keeps_decision_deadline_before_start(stripe_guide, make_departure, make_registration):
    """BUG: moving a date earlier keeps the old (later) decision deadline -> deadline after the new start;
    the hourly job would auto-cancel and refund everyone after the tour already happened."""
    dep = make_departure(stripe_guide, start=in_days(30))
    make_registration(dep)
    dep = departures.move_departure(dep, start_date=in_days(10), end_date=in_days(11))
    dep.refresh_from_db()
    start = timezone.make_aware(timezone.datetime.combine(dep.start_date, timezone.datetime.min.time()))
    assert dep.decision_deadline <= start


def test_move_departure_stops_pending_registrations(stripe_guide, make_departure, make_registration, mailoutbox,
                                                   django_capture_on_commit_callbacks):
    """Questionnaire Q4 (answer B): a guest who is paying while the date moves has the signup called off, is told
    so by email and can sign up again for the new date."""
    dep = make_departure(stripe_guide, start=in_days(30))
    pending = make_registration(dep, status=S.PAYMENT_PENDING, email="p@example.com",
                                hold_until=timezone.now() + timedelta(minutes=20))
    with django_capture_on_commit_callbacks(execute=True):
        departures.move_departure(dep, start_date=in_days(40), end_date=in_days(41))
    pending.refresh_from_db()
    assert pending.status == S.EXPIRED and pending.hold_until is None
    mail = [m for m in mailoutbox if m.to == ["p@example.com"]]
    assert len(mail) == 1 and "abgebrochen" in mail[0].subject and in_days(40).strftime("%d.%m.%Y") in mail[0].body


def test_departure_actions_in_wrong_status(gclient, stripe_guide, make_departure, make_registration):
    dep = make_departure(stripe_guide)
    make_registration(dep)
    url = lambda n: reverse("dashboard:departure_action", args=[dep.pk, n])  # noqa: E731
    assert gclient.post(url("confirm")).status_code == 302
    assert gclient.post(url("confirm")).status_code == 302  # second time: error message
    assert gclient.post(url("cancel"), {}).status_code == 302  # no tick
    dep.refresh_from_db()
    assert dep.status == Departure.Status.CONFIRMED
    assert gclient.post(url("cancel"), {"cancel-sure": "on"}).status_code == 302
    dep.refresh_from_db()
    assert dep.status == Departure.Status.CANCELLED
    for name, data in [("confirm", {}), ("cancel", {"cancel-sure": "on"}), ("unbekannt", {}),
                       ("move", {"move-start_date": in_days(50).isoformat(), "move-end_date": in_days(50).isoformat()}),
                       ("move", {"move-start_date": "x"}), ("nachricht-an-alle", {}),
                       ("nachricht-an-alle", {"all-body": "hallo"})]:
        assert gclient.post(url(name), data).status_code == 302, name
    assert gclient.get(reverse("dashboard:departure", args=[dep.pk])).status_code == 200
    assert gclient.get(reverse("dashboard:departure_edit", args=[dep.pk])).status_code == 200


def test_move_departure_into_past_refused(gclient, stripe_guide, make_departure):
    dep = make_departure(stripe_guide)
    gclient.post(reverse("dashboard:departure_action", args=[dep.pk, "move"]),
                 {"move-start_date": in_days(-1).isoformat(), "move-end_date": in_days(-1).isoformat()})
    dep.refresh_from_db()
    assert dep.start_date == in_days(30)


def _dep_post(dep, **over):
    data = {"title": dep.title, "category": "", "start_date": dep.start_date.isoformat(),
            "end_date": dep.end_date.isoformat(), "price_per_person": str(dep.price_per_person),
            "min_participants": dep.min_participants, "max_participants": dep.max_participants,
            "signup_deadline": timezone.localtime(dep.signup_deadline).strftime("%Y-%m-%dT%H:%M"),
            "decision_deadline": timezone.localtime(dep.decision_deadline).strftime("%Y-%m-%dT%H:%M"),
            "meeting_point": dep.meeting_point, "meeting_time": "08:00", "description": "", "requirements": "",
            "equipment": "", "included": "", "not_included": "", "location": ""}
    data.update(over)
    return data


def test_departure_edit_reduce_seats_below_registrations(gclient, stripe_guide, make_departure, make_registration):
    dep = make_departure(stripe_guide)
    make_registration(dep, seats=3)
    r = gclient.post(reverse("dashboard:departure_edit", args=[dep.pk]), _dep_post(dep, max_participants=2,
                                                                                    min_participants=1))
    assert r.status_code == 200
    dep.refresh_from_db()
    assert dep.max_participants == 4


def test_departure_edit_reduce_seats_below_held_seats(gclient, stripe_guide, make_departure, make_registration):
    """Only a payment in progress (no paid seat yet): max may drop below the held seats."""
    dep = make_departure(stripe_guide)
    make_registration(dep, seats=3, status=S.PAYMENT_PENDING, hold_until=timezone.now() + timedelta(minutes=20))
    gclient.post(reverse("dashboard:departure_edit", args=[dep.pk]), _dep_post(dep, max_participants=2,
                                                                                min_participants=1))
    dep.refresh_from_db()
    assert dep.max_participants >= 3


@pytest.mark.parametrize("over", [
    {"decision_deadline": "2099-01-01T10:00"},  # after start
    {"signup_deadline": "2099-01-01T10:00"},
    {"start_date": "2020-01-01", "end_date": "2020-01-02"},
    {"end_date": "2000-01-01"},
    {"max_participants": 1, "min_participants": 3},
])
def test_departure_new_invalid_dates(gclient, stripe_guide, make_departure, over):
    tmpl = make_departure(stripe_guide)
    n = Departure.objects.count()
    r = gclient.post(reverse("dashboard:departure_new"), _dep_post(tmpl, **over))
    assert r.status_code == 200
    assert Departure.objects.count() == n


def test_departure_new_without_stripe_shows_error(gclient, guide, make_departure):
    tmpl = make_departure(guide)
    r = gclient.post(reverse("dashboard:departure_new"), _dep_post(tmpl))
    assert r.status_code == 200 and Departure.objects.count() == 1


# --- Tour templates -------------------------------------------------------------------------------

def test_tour_delete_keeps_departures_and_bookings(gclient, stripe_guide, tour, make_departure, make_registration,
                                                   make_booking):
    dep = make_departure(stripe_guide, tour=tour)
    reg = make_registration(dep, tour=tour)
    req = make_booking(stripe_guide, in_days(5), tour=tour, status=S.CONFIRMED)
    assert gclient.post(reverse("dashboard:tour_delete", args=[tour.pk])).status_code == 302
    dep.refresh_from_db()
    assert dep.tour is None and dep.title == "Gran Paradiso"
    for p in (reverse("dashboard:departure", args=[dep.pk]), reverse("dashboard:booking", args=[reg.pk]),
              reverse("dashboard:booking", args=[req.pk]), reverse("dashboard:home"), reverse("dashboard:tours")):
        assert gclient.get(p).status_code == 200, p
    assert gclient.post(reverse("dashboard:tour_delete", args=[tour.pk])).status_code == 404


def test_tour_form_invalid(gclient):
    r = gclient.post(reverse("dashboard:tour_new"), {"title": "", "duration_days": "-1", "min_participants": 5,
                                                     "max_participants": 2, "price_per_person": "abc"})
    assert r.status_code == 200 and not Tour.objects.exists()


# --- Drafts ---------------------------------------------------------------------------------------

def test_drafts(gclient, guide, other_guide):
    r = gclient.post(reverse("dashboard:draft_save", args=["unbekannt"]), {"title": "x"})
    assert r.status_code == 400
    r = gclient.post(reverse("dashboard:draft_save", args=["departure"]), {"title": "x", "vorlage": "abc"})
    pk = r.json()["id"]
    assert gclient.get(reverse("dashboard:draft", args=[pk])).status_code == 302
    r = gclient.get(reverse("dashboard:draft", args=[pk]), follow=True)
    assert r.status_code == 200
    gclient.post(reverse("dashboard:draft_save", args=["tour"]), {"title": "t", "entwurf": str(pk)})
    assert Draft.objects.get(pk=pk).kind == "departure"  # other kind -> new draft
    foreign = Draft.objects.create(guide=other_guide, kind="tour", data={})
    assert gclient.get(reverse("dashboard:draft", args=[foreign.pk])).status_code == 404
    assert gclient.post(reverse("dashboard:draft_delete", args=[foreign.pk])).status_code == 404
    assert gclient.get(reverse("dashboard:departure_new"), {"entwurf": "abc"}).status_code == 200
    assert gclient.get(reverse("dashboard:tour_new"), {"entwurf": str(foreign.pk)}).status_code == 200


def test_draft_with_vorlage_of_deleted_tour(gclient, guide, tour):
    d = Draft.objects.create(guide=guide, kind="departure", data={"vorlage": str(tour.pk), "title": "x"})
    tour.delete()
    r = gclient.get(reverse("dashboard:draft", args=[d.pk]), follow=True)
    assert r.status_code in (200, 404)


def test_draft_with_garbage_values_renders(gclient, guide):
    d = Draft.objects.create(guide=guide, kind="departure", data={"start_date": "kaputt", "max_participants": "x",
                                                                  "signup_deadline": "nope"})
    assert gclient.get(reverse("dashboard:departure_new"), {"entwurf": d.pk}).status_code == 200


# --- Pictures -------------------------------------------------------------------------------------

def test_picture_action_unknown_key_404(gclient):
    assert gclient.post(reverse("dashboard:picture_action", args=["gibtsnicht", "loeschen"])).status_code == 404


def test_picture_action_stock_or_foreign_404(gclient, other_guide):
    stock = Picture.objects.create(data=b"x", in_library=True)
    foreign = Picture.objects.create(guide=other_guide, data=b"x", in_library=True)
    for p in (stock, foreign):
        assert gclient.post(reverse("dashboard:picture_action", args=[p.key, "loeschen"])).status_code == 404


def test_picture_actions(gclient, guide, tour):
    pic = Picture.objects.create(guide=guide, data=b"x", in_library=True)
    tour.picture = pic
    tour.save()
    assert gclient.post(reverse("dashboard:picture_action", args=[pic.key, "speichern"]),
                        {"category": "ungueltig", "title": "x"}).status_code == 302
    assert gclient.post(reverse("dashboard:picture_action", args=[pic.key, "foo"])).status_code == 302
    assert gclient.get(reverse("dashboard:pictures")).status_code == 200
    r = gclient.post(reverse("dashboard:picture_action", args=[pic.key, "loeschen"]))
    assert r.status_code == 302
    tour.refresh_from_db()
    assert tour.picture is None
    assert gclient.post(reverse("dashboard:pictures"), {}).status_code == 302


# --- Availability / partners / external -----------------------------------------------------------

def test_availability_forms(gclient, guide, other_guide):
    url = reverse("dashboard:availability")
    for data in [{"form": "block", "block-start_date": in_days(5).isoformat(), "block-end_date": in_days(3).isoformat()},
                 {"form": "open", "open-start_date": "x"}, {"form": "partner"}, {"form": "partner_period"},
                 {"form": "unbekannt"}, {}]:
        assert gclient.post(url, data).status_code == 200
    for _ in range(2):  # overlapping blocks
        assert gclient.post(url, {"form": "block", "block-start_date": in_days(5).isoformat(),
                                  "block-end_date": in_days(7).isoformat()}).status_code == 302
    p_other = Partner.objects.create(guide=other_guide, name="Fremd", email="f@example.com")
    r = gclient.post(url, {"form": "partner_period", "pp-partner": p_other.pk,
                           "pp-start_date": in_days(5).isoformat(), "pp-end_date": in_days(6).isoformat()})
    assert r.status_code == 200 and not PartnerPeriod.objects.exists()
    b = BlockedPeriod.objects.filter(guide=guide).first()
    assert gclient.post(reverse("dashboard:block_delete", args=[b.pk])).status_code == 302
    assert gclient.post(reverse("dashboard:block_delete", args=[b.pk])).status_code == 404
    assert gclient.post(reverse("dashboard:open_delete", args=[999999])).status_code == 404
    assert gclient.post(reverse("dashboard:partner_delete", args=[p_other.pk])).status_code == 404
    assert gclient.get(url).status_code == 200


def test_calendar_sync_without_calendar(gclient):
    r = gclient.post(reverse("dashboard:calendar_sync"), follow=True)
    assert r.status_code == 200
    r = gclient.post(reverse("dashboard:availability"), {"form": "ical", "ical-ical_url": ""}, follow=True)
    assert r.status_code == 200


def _partner_offer(guide, status=ExternalTour.Status.PENDING):
    partner = Partner.objects.create(guide=guide, name="Alpinschule", email="a@example.com")
    return partner, ExternalTour.objects.create(guide=guide, partner=partner, provider="Alpinschule", title="Eiger",
                                                start_date=in_days(20), end_date=in_days(21), status=status,
                                                contact_email="k@example.com")


@pytest.mark.parametrize("status", list(ExternalTour.Status.values))
@pytest.mark.parametrize("action", ["annehmen", "ablehnen", "zeigen", "verbergen", "loeschen", "unbekannt"])
def test_external_tour_actions_any_status(gclient, guide, status, action):
    _partner, t = _partner_offer(guide, status)
    assert gclient.post(reverse("dashboard:external_tour_action", args=[t.pk, action])).status_code == 302


def test_external_tour_show_on_declined_offer_has_no_effect(gclient, guide):
    """'zeigen' works on any status; a declined/cancelled tour gets show_to_guests=True (harmless, is_public checks)."""
    _partner, t = _partner_offer(guide, ExternalTour.Status.DECLINED)
    gclient.post(reverse("dashboard:external_tour_action", args=[t.pk, "zeigen"]))
    t.refresh_from_db()
    assert not t.is_public


def test_partner_delete_with_pending_offer_then_answer(gclient, guide):
    partner, t = _partner_offer(guide)
    gclient.post(reverse("dashboard:partner_delete", args=[partner.pk]))
    assert gclient.get(reverse("dashboard:external_tours")).status_code == 200
    assert gclient.post(reverse("dashboard:external_tour_action", args=[t.pk, "annehmen"])).status_code == 302
    t.refresh_from_db()
    assert t.status == ExternalTour.Status.CONFIRMED


def test_accept_partner_offer_on_meanwhile_blocked_days(gclient, guide):
    """Accepting a pending offer doesn't re-check the days (guide blocked them meanwhile)."""
    _partner, t = _partner_offer(guide)
    BlockedPeriod.objects.create(guide=guide, start_date=in_days(20), end_date=in_days(20))
    gclient.post(reverse("dashboard:external_tour_action", args=[t.pk, "annehmen"]))
    t.refresh_from_db()
    assert t.status == ExternalTour.Status.PENDING


# --- Notifications, guests, settings, erase -----------------------------------------------------------

def test_notifications(gclient, guide, other_guide, make_booking):
    b = make_booking(guide, in_days(5))
    n = Notification.objects.create(guide=guide, booking=b, kind=Notification.Kind.MESSAGE)
    n2 = Notification.objects.create(guide=other_guide, kind=Notification.Kind.MESSAGE)
    assert gclient.get(reverse("dashboard:notification", args=[n.pk]))["Location"].endswith("#nachrichten")
    assert gclient.get(reverse("dashboard:notification", args=[n2.pk])).status_code == 404
    r = gclient.post(reverse("dashboard:notifications_read"), {"next": "https://evil.example/"})
    assert r["Location"] == reverse("dashboard:home")
    r = gclient.post(reverse("dashboard:notifications_read"), {"next": "/dashboard/../../evil"})
    assert r.status_code == 302


def test_guests_page_with_query_and_erased_data(gclient, stripe_guide, make_departure, make_registration):
    dep = make_departure(stripe_guide)
    make_registration(dep, email="Gast@Example.com")
    make_registration(dep, email="gast@example.com", guest_name="Gast B")
    reauth(gclient)
    gclient.post(reverse("dashboard:erase_guest_data"), {"email": "gast@example.com"})
    assert all(not b.guest_phone for b in Booking.objects.all())
    for q in ("", "gast", "%", "<script>"):
        assert gclient.get(reverse("dashboard:guests"), {"q": q}).status_code == 200
    assert gclient.post(reverse("dashboard:erase_guest_data"), {"email": "kein"}).status_code == 302


def test_settings_post_invalid(gclient):
    r = gclient.post(reverse("dashboard:settings"), {"display_name": ""})
    assert r.status_code == 200


# --- Lots of data ------------------------------------------------------------------------------------

def test_pages_with_lots_of_data(gclient, stripe_guide, tour, make_booking, make_departure, make_registration):
    g = stripe_guide
    for i, status in enumerate(S.values):
        make_booking(g, in_days(-40 + i * 3), status=status, price=Decimal("300"), tour=tour if i % 2 else None,
                     guest_email=f"g{i}@example.com")
    for i in range(6):
        dep = make_departure(g, start=in_days(5 + i * 4))
        make_registration(dep, email=f"r{i}@example.com")
        make_registration(dep, status=S.CANCELLED, email=f"c{i}@example.com")
    Departure.objects.create(guide=g, title="Alt", start_date=in_days(-20), end_date=in_days(-19),
                             price_per_person=1, min_participants=1, max_participants=2,
                             signup_deadline=at(in_days(-25)), decision_deadline=at(in_days(-24)), meeting_point="x")
    BlockedPeriod.objects.create(guide=g, start_date=in_days(1), end_date=in_days(60))
    _partner_offer(g)
    ExternalTour.objects.create(guide=g, title="Ext", start_date=in_days(-5), end_date=in_days(-5))
    for i in range(30):
        Notification.objects.create(guide=g, kind=Notification.Kind.MESSAGE)
    reauth(gclient)
    for name in EMPTY_PAGES + ["export"]:
        assert gclient.get(reverse(f"dashboard:{name}")).status_code == 200, name
    for m in ("2026-10", "2026-11", "2026-12", "2027-01"):
        assert gclient.get(reverse("dashboard:calendar"), {"monat": m}).status_code == 200
    for b in Booking.objects.all():
        assert gclient.get(reverse("dashboard:booking", args=[b.pk])).status_code == 200
    for d in Departure.objects.all():
        assert gclient.get(reverse("dashboard:departure", args=[d.pk])).status_code == 200
        assert gclient.get(reverse("dashboard:departure_edit", args=[d.pk])).status_code == 200
    r = gclient.get(reverse("dashboard:export"), {"start": "2020-01-01", "end": "2030-12-31", "format": "xlsx"})
    assert r.status_code == 200
