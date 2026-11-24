"""Guide feedback 2026-11-24: to-do list, bell, booking page, tour sheet, group bookings, rates, guest file, newsletter."""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.urls import reverse

from accounts.models import DayRateTier
from bookings import departures, emails, services
from bookings.models import Booking, Companion, GuestProfile, Notification, Subscriber, TodoDismissal

from .conftest import DETAILS, at, confirm_offer, in_days
from .test_views import signup_post

S = Booking.Status


@pytest.fixture
def gclient(client, guide):
    client.force_login(guide)
    return client


# --- To-do list ---------------------------------------------------------------------------------


def test_todo_item_can_be_clicked_away(gclient, guide, make_booking):
    done = make_booking(guide, in_days(-1), status=S.CONFIRMED, price=Decimal("500"))
    html = gclient.get(reverse("dashboard:home")).content.decode()
    assert f"invoice-{done.pk}" in html and 'class="count-dot"' in html
    gclient.post(reverse("dashboard:todo_dismiss"), {"key": f"invoice-{done.pk}"})
    assert TodoDismissal.objects.filter(guide=guide, key=f"invoice-{done.pk}").exists()
    html = gclient.get(reverse("dashboard:home")).content.decode()
    assert f"invoice-{done.pk}" not in html


def test_open_requests_cannot_be_clicked_away(gclient, guide, make_booking):
    make_booking(guide, in_days(10))
    html = gclient.get(reverse("dashboard:home")).content.decode()
    assert "Anfrage beantworten" in html and 'name="key"' not in html


def test_upcoming_tours_show_three_and_fold_the_rest(gclient, guide, make_booking):
    for i in range(5):
        make_booking(guide, in_days(10 + i), status=S.CONFIRMED, price=Decimal("500"))
    html = gclient.get(reverse("dashboard:home")).content.decode()
    assert "2 weitere Touren anzeigen" in html
    assert html.count("#notizen") == 5  # a note link on every tour


# --- Bell -----------------------------------------------------------------------------------------


def test_guest_cancel_decline_and_payment_ring_the_bell(guide, make_booking):
    offered = make_booking(guide, in_days(10), status=S.OFFERED, price=Decimal("500"), deadline=None)
    services.decline_offer(offered)
    confirmed = make_booking(guide, in_days(20), status=S.CONFIRMED)
    services.guest_cancel(confirmed)
    billed = make_booking(guide, in_days(-3), status=S.AWAITING_PAYMENT, price=Decimal("500"))
    services.mark_paid_by_stripe(billed, "cs_1")
    kinds = set(Notification.objects.values_list("kind", flat=True))
    assert {"declined", "cancelled", "paid"} <= kinds


def test_bell_lists_read_ones_too_and_counts_unread(gclient, guide, make_booking):
    b = make_booking(guide, in_days(10))
    Notification.objects.create(guide=guide, booking=b, kind="request")
    Notification.objects.create(guide=guide, booking=b, kind="paid", read_at=b.created_at)
    html = gclient.get(reverse("dashboard:guests")).content.decode()
    assert 'class="notif-count">1<' in html
    assert "hat bezahlt" in html and 'class="unread"' in html


# --- Booking page ------------------------------------------------------------------------------------


def test_guide_names_the_tour_and_the_original_request_stays(gclient, guide, make_booking):
    b = make_booking(guide, in_days(10), status=S.CONFIRMED, guest_message="irgendeine Skitour Zentralschweiz")
    gclient.post(reverse("dashboard:booking_action", args=[b.pk, "rename"]), {"title-custom_title": "Skitour Rossstock"})
    b.refresh_from_db()
    assert b.title == "Skitour Rossstock" and b.guest_message == "irgendeine Skitour Zentralschweiz"
    assert "Rossstock" in b.events.last().note
    html = gclient.get(reverse("dashboard:booking", args=[b.pk])).content.decode()
    assert "Ursprüngliche Anfrage" in html and "irgendeine Skitour Zentralschweiz" in html


def test_note_per_person_shows_in_notes(gclient, guide, make_booking):
    b = make_booking(guide, in_days(10), status=S.CONFIRMED)
    c = Companion.objects.create(booking=b, name="Max")
    gclient.post(reverse("dashboard:booking_action", args=[b.pk, f"person_note-{c.pk}"]), {"note": "braucht Steigeisen"})
    gclient.post(reverse("dashboard:booking_action", args=[b.pk, "person_note-booker"]), {"note": "Pickel"})
    c.refresh_from_db()
    b.refresh_from_db()
    assert (c.guide_note, b.booker_note) == ("braucht Steigeisen", "Pickel")
    html = gclient.get(reverse("dashboard:booking", args=[b.pk])).content.decode()
    assert "Max:</strong> braucht Steigeisen" in html


def test_participant_count_mismatch_suggests_adjusting(gclient, guide, make_booking):
    b = make_booking(guide, in_days(10), status=S.CONFIRMED, participants=1, price=Decimal("750"))
    Companion.objects.create(booking=b, name="Max")
    html = gclient.get(reverse("dashboard:booking", args=[b.pk])).content.decode()
    assert "?anpassen=2#aendern" in html
    html = gclient.get(reverse("dashboard:booking", args=[b.pk]) + "?anpassen=2").content.decode()
    assert 'name="change-participants" value="2"' in html


def test_ask_details_button_is_gone(gclient, guide, make_booking):
    b = make_booking(guide, in_days(10), status=S.CONFIRMED)
    assert "ask_details" not in gclient.get(reverse("dashboard:booking", args=[b.pk])).content.decode()


def test_billing_possible_on_the_tour_day_not_before(gclient, guide, make_booking):
    today = make_booking(guide, in_days(0), end_date=in_days(2), status=S.CONFIRMED, price=Decimal("500"))
    assert gclient.get(reverse("dashboard:invoice", args=[today.pk])).status_code == 200
    later = make_booking(guide, in_days(5), status=S.CONFIRMED, price=Decimal("500"))
    response = gclient.get(reverse("dashboard:invoice", args=[later.pk]), follow=True)
    assert "ab dem Tourentag" in response.content.decode()


def test_tour_sheet_is_a_pdf(gclient, guide, make_booking):
    b = make_booking(guide, in_days(10), status=S.CONFIRMED, guide_notes="Lawinenbulletin – prüfen", meeting="Bhf")
    Companion.objects.create(booking=b, name="Pamela Müller", guide_note="Pickel")
    response = gclient.get(reverse("dashboard:booking_sheet", args=[b.pk]))
    assert response.status_code == 200 and response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")


def test_departure_sheet_and_notes(gclient, stripe_guide, make_departure, make_registration):
    gclient.force_login(stripe_guide)
    d = make_departure(stripe_guide)
    make_registration(d, seats=2)
    gclient.post(reverse("dashboard:departure_action", args=[d.pk, "notizen"]), {"notes-guide_notes": "Seil"})
    d.refresh_from_db()
    assert d.guide_notes == "Seil"
    assert gclient.get(reverse("dashboard:departure_sheet", args=[d.pk])).content.startswith(b"%PDF")


def test_guest_replies_to_automatic_emails_reach_the_booking(guide, make_booking, settings, mailoutbox,
                                                           django_capture_on_commit_callbacks):
    settings.REPLY_EMAIL_DOMAIN = "post.tourchef.test"
    b = make_booking(guide, in_days(10), status=S.CONFIRMED)
    with django_capture_on_commit_callbacks(execute=True):
        services.guide_cancel(b, "Lawinengefahr")
    to_guest = [m for m in mailoutbox if m.to == ["gast@example.com"]]
    assert to_guest and to_guest[0].reply_to[0].endswith("@post.tourchef.test")


# --- Guest: group bookings ---------------------------------------------------------------------------


def test_group_yes_needs_the_forward_tick(client, guide, make_booking):
    b = make_booking(guide, in_days(10), status=S.OFFERED, participants=3, price=Decimal("900"),
                     deadline=None)
    page = client.get(reverse("guest:detail", args=[b.token]) + "?zusagen=1").content.decode()
    assert b.companion_url in page
    assert client.post(reverse("guest:details", args=[b.token]), DETAILS).status_code == 400
    b.refresh_from_db()
    assert b.status == S.OFFERED
    confirm_offer(client, b)
    b.refresh_from_db()
    assert b.status == S.CONFIRMED


def test_companions_give_experience_but_no_billing_address(client, guide, make_booking):
    b = make_booking(guide, in_days(10), status=S.CONFIRMED, participants=2)
    url = b.companion_url.replace("https://tourchef.test", "")
    page = client.get(url).content.decode()
    assert "billing_address" not in page and "angaben-experience" in page
    data = {k: v for k, v in DETAILS.items() if k != "angaben-billing_address"}
    data.update({"angaben-name": "Max", "angaben-phone_0": "41", "angaben-phone_1": "079 555 55 55",
                 "angaben-news": "on"})
    client.post(url, data)
    c = Companion.objects.get()
    assert c.saved_details()["experience"] == "Mehrere Hochtouren, gute Kondition"
    assert Notification.objects.filter(kind="details").exists()


# --- Rates ---------------------------------------------------------------------------------------


def test_rate_table_per_group_size(gclient, guide):
    data = {k: v for k, v in gclient.get(reverse("dashboard:settings")).context["form"].initial.items()
            if v is not None and k not in ("current_password",)}
    data = {k: v for k, v in data.items() if not isinstance(v, (list, dict))}
    data.update({"day_rate": "700", "tiers-rate_3": "800", "tiers-rate_6": "950", "tiers-size_0": "12",
                 "tiers-extra_rate_0": "1400"})
    response = gclient.post(reverse("dashboard:settings"), data)
    assert response.status_code == 302, response.context["form"].errors if response.context else ""
    guide.refresh_from_db()
    assert [(t.min_participants, t.day_rate) for t in DayRateTier.objects.filter(guide=guide)] == [
        (3, 800), (6, 950), (12, 1400)]
    assert [guide.rate_for(n) for n in (1, 2, 3, 5, 6, 11, 12)] == [700, 700, 800, 800, 950, 950, 1400]


def test_rate_table_needs_size_and_rate_together(guide):
    from accounts.forms import RateTableForm

    form = RateTableForm({"tiers-size_0": "10"}, guide=guide, prefix="tiers")
    assert not form.is_valid()


# --- Guest file and newsletter -------------------------------------------------------------------


def test_news_opt_in_new_tour_mail_and_unsubscribe(client, stripe_guide, make_booking, make_departure, mailoutbox):
    b = make_booking(stripe_guide, in_days(10), status=S.OFFERED, price=Decimal("750"), deadline=None,
                     participants=1)
    confirm_offer(client, b, **{"angaben-news": "on"})
    sub = Subscriber.objects.get()
    assert sub.email == "gast@example.com"
    mailoutbox.clear()
    assert emails.new_tour_announced(make_departure(stripe_guide)) == 1
    assert sub.unsubscribe_url in mailoutbox[-1].body
    url = reverse("unsubscribe", args=[sub.token])
    assert client.get(url).status_code == 200 and Subscriber.objects.exists()  # mail scanners only GET
    client.post(url)
    assert not Subscriber.objects.exists()


def test_create_departure_announces_to_subscribers(stripe_guide, django_capture_on_commit_callbacks, mailoutbox):
    Subscriber.subscribe(stripe_guide, "fan@example.com", "Fan")
    start = in_days(40)
    with django_capture_on_commit_callbacks(execute=True):
        departures.create_departure(
            stripe_guide, title="Spontan: Gross Lohner", start_date=start, end_date=start,
            price_per_person=Decimal("300"), min_participants=1, max_participants=4,
            signup_deadline=at(start - timedelta(days=2)), decision_deadline=at(start - timedelta(days=1)))
    assert any("Gross Lohner" in m.subject and m.to == ["fan@example.com"] for m in mailoutbox)


def test_newsletter_from_guest_file(gclient, guide, make_booking, mailoutbox):
    make_booking(guide, in_days(10), status=S.CONFIRMED)
    Subscriber.subscribe(guide, "gast@example.com", "Gast")
    html = gclient.get(reverse("dashboard:guests")).content.decode()
    assert "1 Gast möchte informiert werden" in html and 'class="guest"' in html
    gclient.post(reverse("dashboard:guests"), {"news-subject": "Spontantour Samstag", "news-body": "Wer kommt mit?"})
    assert mailoutbox[-1].subject == "Spontantour Samstag" and "Wer kommt mit?" in mailoutbox[-1].body


def test_guest_search_finds_tour_title(gclient, guide, make_booking):
    make_booking(guide, in_days(10), status=S.CONFIRMED, custom_title="Skitour Rossstock", guest_name="Max")
    html = gclient.get(reverse("dashboard:guests") + "?q=rossstock").content.decode()
    assert "Max" in html


# --- Scheduled dates: details before paying ------------------------------------------------------------


def test_signup_takes_planning_details_before_payment(client, stripe_guide, make_departure):
    departure = make_departure(stripe_guide)
    with mock.patch("stripe.checkout.Session.create", return_value=mock.Mock(id="cs", url="https://checkout.stripe.com/x")):
        response = signup_post(client, departure, **{"angaben-diet": "vegetarian", "angaben-keep": "on"})
    assert response.status_code == 302
    booking = Booking.objects.get()
    assert booking.saved_details()["diet"] == "vegetarian" and booking.experience == "Allalin"
    booking.status = S.PAID
    booking.save()
    assert not booking.details_missing
    assert GuestProfile.objects.filter(email="neu@example.com").exists()
