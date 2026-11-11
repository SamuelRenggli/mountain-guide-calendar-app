"""QA probes: money, dates/time, concurrency, languages. Each test asserts the EXPECTED behaviour;
a failing test documents a bug."""

import gettext
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from bookings import departures, emails, export, services
from bookings.models import Booking, InvoiceLine, Refund, money
from payments import stripe_service

from .conftest import in_days

S = Booking.Status
ZRH = ZoneInfo("Europe/Zurich")


def _signup(departure, seats=1, lang="de"):
    return departures.start_registration(
        departure, seats=seats, guest_name="G", guest_email="g@example.com", guest_phone="1",
        emergency_contact="x", experience="y", language=lang)


# --- Money -----------------------------------------------------------------------------


def test_payment_after_move_is_refunded(stripe_guide, make_departure):
    """Questionnaire Q4 (answer B): the guest was paying while the guide moved the date. The signup was called off;
    a payment that still lands is refunded in full instead of registering the guest for the old date."""
    dep = make_departure(stripe_guide)
    booking = _signup(dep)
    new_start = in_days(40)
    departures.move_departure(dep, start_date=new_start, end_date=new_start + timedelta(days=1))
    booking.refresh_from_db()
    assert booking.status == S.EXPIRED
    paid = departures.registration_paid(booking, session_id="cs_1", payment_intent="pi_1",
                                        amount_cents=booking.amount_cents)
    paid.refresh_from_db()
    assert paid.status == S.EXPIRED and paid.refund_due == paid.amount_paid > 0


def test_second_payment_does_not_swallow_first_refund(stripe_guide, make_departure, make_registration,
                                                      django_capture_on_commit_callbacks):
    """A second paid checkout for an already paid registration is refunded; a later free cancellation must still
    refund the FIRST payment (pi_test)."""
    dep = make_departure(stripe_guide)
    booking = make_registration(dep)  # paid 480 via pi_test, start in 30 days -> free cancellation
    with mock.patch("stripe.Refund.create") as refund, django_capture_on_commit_callbacks(execute=True):
        departures.registration_paid(booking, session_id="cs_2", payment_intent="pi_2", amount_cents=48000)
    with mock.patch("stripe.Refund.create") as refund2, django_capture_on_commit_callbacks(execute=True):
        departures.guest_cancel(booking)
    calls = [(c.kwargs["payment_intent"], c.kwargs["amount"]) for c in refund.call_args_list + refund2.call_args_list]
    assert ("pi_test", 48000) in calls, f"refunds sent: {calls}"


def test_guest_page_fee_matches_charged_fee(client, stripe_guide, make_departure, make_registration):
    """The guest page preview of the cancellation fee must equal what guest_cancel charges (ROUND_HALF_UP)."""
    dep = make_departure(stripe_guide, start=in_days(10), price_per_person=Decimal("12.25"))
    booking = make_registration(dep)
    expected_fee = money(booking.amount_paid * services.cancellation_percent(booking) / 100)
    assert expected_fee == Decimal("6.13")
    response = client.get(reverse("guest:detail", args=[booking.token]))
    assert response.context["fee"] == expected_fee, f"page shows {response.context['fee']}"
    assert response.context["fee"] + response.context["refund"] == booking.amount_paid


def test_reinvoice_expires_open_checkout(stripe_guide, make_booking):
    """Correcting the bill must invalidate the checkout session with the old amount."""
    booking = make_booking(stripe_guide, in_days(-3), status=S.CONFIRMED, price=600)
    booking.end_date = in_days(-3)
    booking.save()
    booking = services.invoice(booking, [InvoiceLine(kind=InvoiceLine.Kind.TOUR, quantity=1, unit_price=600,
                                                     description="Tour")])
    with mock.patch("stripe.checkout.Session.create", return_value=mock.Mock(id="cs_old", url="u")):
        stripe_service.create_checkout_url(booking)
    booking.refresh_from_db()
    with mock.patch("stripe.checkout.Session.expire") as expire, \
            mock.patch("stripe.checkout.Session.retrieve", return_value=mock.Mock(id="cs_old", status="open")):
        services.invoice(booking, [InvoiceLine(kind=InvoiceLine.Kind.TOUR, quantity=1, unit_price=700,
                                               description="Tour")])
    assert expire.called, "old checkout (CHF 600) stays payable after the bill was corrected to CHF 700"


def test_zero_invoice_settles_the_tour(stripe_guide, make_booking, mailoutbox, django_capture_on_commit_callbacks):
    """Questionnaire Q6 (answer A): a bill over CHF 0 is possible and the tour counts as settled at once."""
    booking = make_booking(stripe_guide, in_days(-1), status=S.CONFIRMED, price=600)
    with django_capture_on_commit_callbacks(execute=True):
        booking = services.invoice(booking, [InvoiceLine(kind=InvoiceLine.Kind.TOUR, quantity=1, unit_price=0,
                                                         description="Tour")])
    booking.refresh_from_db()
    assert booking.status == S.PAID and booking.price == 0 and booking.paid_at and booking.is_invoiced
    assert not mailoutbox  # no bill with a payment link


def test_platform_fee_rounding():
    with mock.patch.object(settings, "PLATFORM_FEE_PERCENT", "2.5"):
        assert stripe_service.platform_fee_cents(4805) == 120  # 120.125 -> 120
        assert stripe_service.platform_fee_cents(5) == 0


def test_refund_extra_limits(stripe_guide, make_departure, make_registration, django_capture_on_commit_callbacks):
    dep = make_departure(stripe_guide, start=in_days(5))
    booking = make_registration(dep)  # 100 % fee window
    with mock.patch("stripe.Refund.create"), django_capture_on_commit_callbacks(execute=True):
        booking = departures.guest_cancel(booking)
    booking.refresh_from_db()
    assert booking.refunded_amount == 0
    with pytest.raises(services.BookingError):
        departures.refund_extra(booking, Decimal("480.01"))
    with pytest.raises(services.BookingError):
        departures.refund_extra(booking, Decimal("-5"))
    with mock.patch("stripe.Refund.create") as refund, django_capture_on_commit_callbacks(execute=True):
        departures.refund_extra(booking, Decimal("480"))
    assert refund.call_args.kwargs["amount"] == 48000


def test_export_sums(stripe_guide, make_departure, make_registration, make_booking):
    """Questionnaire Q7: payments with Tourchef fee and payout, refunds as their own negative rows."""
    dep = make_departure(stripe_guide)
    reg = make_registration(dep, seats=2, refunded_amount=Decimal("100"), refund_due=Decimal("50"))
    Refund.objects.create(booking=reg, amount=Decimal("100"))
    b = make_booking(stripe_guide, in_days(-5), status=S.PAID, price=Decimal("850.50"), amount_paid=Decimal("850.50"),
                     paid_at=timezone.now(), payment_method="stripe")
    InvoiceLine.objects.create(booking=b, position=0, kind=InvoiceLine.Kind.TOUR, quantity=1, unit_price=Decimal("750.00"),
                               description="Tour")
    InvoiceLine.objects.create(booking=b, position=1, kind=InvoiceLine.Kind.EXPENSE, quantity=1,
                               unit_price=Decimal("100.50"), description="Hütte")
    with mock.patch.object(settings, "PLATFORM_FEE_PERCENT", "2.5"):
        header, rows = export.rows(stripe_guide, in_days(-1), in_days(1))
    assert "Tourchef-Gebühr CHF" in header and "Offene Rückzahlung CHF" in header
    assert len(rows) == 3
    by_amount = {r[6]: r for r in rows}
    assert by_amount[Decimal("960.00")][8] == Decimal("24.00")  # fee
    assert by_amount[Decimal("960.00")][9] == Decimal("936.00")  # payout
    assert by_amount[Decimal("960.00")][10] == Decimal("50.00")  # refund still open
    assert by_amount[Decimal("850.50")][7] == Decimal("100.50")
    refund = by_amount[Decimal("-100.00")]
    assert refund[2] == "Rückzahlung" and refund[8] == Decimal("-2.50") and refund[9] == Decimal("-97.50")
    resp = export.response(stripe_guide, in_days(-1), in_days(1), "csv")
    assert resp.status_code == 200


def test_export_books_refund_in_its_own_month(stripe_guide, make_booking):
    b = make_booking(stripe_guide, date(2026, 6, 10), status=S.PAID, price=500, amount_paid=500,
                     paid_at=datetime(2026, 6, 12, 10, tzinfo=ZRH), payment_method="manual", refunded_amount=200)
    Refund.objects.create(booking=b, amount=200, created_at=datetime(2026, 7, 3, 9, tzinfo=ZRH))
    _, june = export.rows(stripe_guide, date(2026, 6, 1), date(2026, 6, 30))
    _, july = export.rows(stripe_guide, date(2026, 7, 1), date(2026, 7, 31))
    assert [r[6] for r in june] == [Decimal("500.00")] and [r[6] for r in july] == [Decimal("-200.00")]


def test_export_paid_just_after_midnight_counts_for_local_day(stripe_guide, make_booking):
    paid_at = datetime(2026, 7, 1, 0, 30, tzinfo=ZRH)
    make_booking(stripe_guide, date(2026, 6, 28), status=S.PAID, price=100, amount_paid=100, paid_at=paid_at,
                 payment_method="manual")
    _, june = export.rows(stripe_guide, date(2026, 6, 1), date(2026, 6, 30))
    _, july = export.rows(stripe_guide, date(2026, 7, 1), date(2026, 7, 31))
    assert (len(june), len(july)) == (0, 1)


# --- Date / time -----------------------------------------------------------------------


def test_receipt_shows_local_payment_date(stripe_guide, make_departure, make_registration):
    """Paid at 00:30 Zurich time on 1 July: the receipt must say 01.07.2026, not the UTC date."""
    dep = make_departure(stripe_guide)
    booking = make_registration(dep, paid_at=datetime(2026, 7, 1, 0, 30, tzinfo=ZRH))
    booking.refresh_from_db()  # DB returns UTC like in production
    text = emails._receipt(booking)
    assert "01.07.2026" in text, text


def test_deadline_shown_in_local_time():
    value = datetime(2026, 3, 29, 1, 30, tzinfo=ZoneInfo("UTC"))  # 03:30 CEST after the switch
    assert emails._deadline(value) == "29.03.2026 03:30"


def test_cancellation_tiers_at_boundaries(stripe_guide, make_booking):
    b = make_booking(stripe_guide, date(2027, 1, 15), status=S.CONFIRMED, price=1000,
                     cancel_free_days=14, cancel_partial_days=7, cancel_partial_percent=50)
    assert services.cancellation_percent(b, date(2027, 1, 1)) == 0  # 14 days before
    assert services.cancellation_percent(b, date(2027, 1, 2)) == 50  # 13
    assert services.cancellation_percent(b, date(2027, 1, 8)) == 50  # 7
    assert services.cancellation_percent(b, date(2027, 1, 9)) == 100  # 6


def test_auto_cancel_after_decision_deadline(stripe_guide, make_departure, make_registration,
                                             django_capture_on_commit_callbacks):
    dep = make_departure(stripe_guide)
    reg = make_registration(dep)
    dep.decision_deadline = timezone.now() - timedelta(minutes=1)
    dep.save()
    with mock.patch("stripe.Refund.create") as refund, django_capture_on_commit_callbacks(execute=True):
        assert departures.remind_and_auto_cancel() == (0, 1)
    reg.refresh_from_db()
    assert reg.status == S.CANCELLED and reg.refunded_amount == Decimal("480.00")
    assert refund.call_args.kwargs["amount"] == 48000


def test_multi_day_request_over_year_end(stripe_guide):
    stripe_guide.max_advance_days = 730
    stripe_guide.save()
    start = date(timezone.localdate().year + 1, 12, 30)
    with mock.patch("bookings.availability.range_bookable", return_value=True):
        b = services.create_request(guide=stripe_guide, start_date=start, end_date=start + timedelta(days=3),
                                    participants=2, guest_name="G", guest_email="g@example.com")
    assert b.days == 4


# --- Concurrency / integrity ------------------------------------------------------------


def test_signup_never_exceeds_seats(stripe_guide, make_departure):
    dep = make_departure(stripe_guide)  # max 4
    _signup(dep, seats=3)
    with pytest.raises(services.BookingError):
        _signup(dep, seats=2)
    _signup(dep, seats=1)
    assert dep.seats_taken() == 4


def test_late_payment_when_full_is_refunded(stripe_guide, make_departure, make_registration,
                                            django_capture_on_commit_callbacks):
    dep = make_departure(stripe_guide)
    late = _signup(dep, seats=1)
    Booking.objects.filter(pk=late.pk).update(status=S.EXPIRED, hold_until=None)
    make_registration(dep, seats=4, email="x@example.com")
    with mock.patch("stripe.Refund.create") as refund, django_capture_on_commit_callbacks(execute=True):
        departures.registration_paid(late, session_id="cs", payment_intent="pi_l", amount_cents=48000)
    late.refresh_from_db()
    assert late.status == S.EXPIRED and refund.call_args.kwargs["amount"] == 48000
    assert dep.seats_taken() == 4


# --- Languages -------------------------------------------------------------------------

PLACEHOLDER = re.compile(r"%\((\w+)\)[sdf]|\{(\w+)\}")


def _names(text):
    return {a or b for a, b in PLACEHOLDER.findall(text)}


@pytest.mark.parametrize("lang", ["en", "fr", "it"])
def test_translation_placeholders_match(lang):
    path = Path(settings.BASE_DIR) / "locale" / lang / "LC_MESSAGES" / "django.mo"
    with path.open("rb") as fh:
        catalog = gettext.GNUTranslations(fh)._catalog
    bad = []
    for msgid, msgstr in catalog.items():
        if not msgid or not msgstr:
            continue
        if isinstance(msgid, tuple):
            continue
        if _names(msgstr) - _names(msgid):
            bad.append((msgid[:60], sorted(_names(msgstr) - _names(msgid))))
    assert not bad, bad


@pytest.mark.parametrize("lang", ["en", "fr", "it"])
def test_mo_matches_po(lang):
    base = Path(settings.BASE_DIR) / "locale" / lang / "LC_MESSAGES"
    po = (base / "django.po").read_text(encoding="utf-8")
    assert "#, fuzzy" not in po.split("\n\n", 1)[1]


@pytest.mark.parametrize("lang", ["de", "en", "fr", "it"])
def test_guest_and_public_pages_render(client, lang, stripe_guide, make_departure, make_registration, make_booking):
    dep = make_departure(stripe_guide)
    pages = [reverse("widget:departure", args=[dep.public_id]), reverse("widget:show", args=[stripe_guide.slug]),
             reverse("widget:terms", args=[stripe_guide.slug])]
    reg = make_registration(dep)
    pages.append(reverse("guest:detail", args=[reg.token]))
    for i, status in enumerate([S.REQUESTED, S.OFFERED, S.CONFIRMED, S.RESCHEDULE_PROPOSED, S.AWAITING_PAYMENT,
                                S.PAID, S.CANCELLED]):
        b = make_booking(stripe_guide, in_days(20 + 2 * i), status=status, price=500, deadline=timezone.now() + timedelta(days=2),
                         estimated_expenses=Decimal("80"), invoiced_at=timezone.now() if status == S.AWAITING_PAYMENT else None)
        pages.append(reverse("guest:detail", args=[b.token]))
    for url in pages:
        response = client.get(url + f"?lang={lang}")
        assert response.status_code == 200, (url, response.status_code)
        assert response.headers.get("Content-Language", lang) == lang


@pytest.mark.parametrize("lang", ["en", "fr", "it"])
def test_emails_in_guest_language(lang, stripe_guide, make_departure, make_registration, make_booking, mailoutbox):
    dep = make_departure(stripe_guide)
    reg = make_registration(dep, language=lang)
    b = make_booking(stripe_guide, in_days(20), status=S.OFFERED, price=500, language=lang,
                     deadline=timezone.now() + timedelta(days=2), estimated_expenses=Decimal("80"))
    with mock.patch("bookings.emails.logger") as log:
        for func in (emails.request_created, emails.offer_sent, emails.offer_accepted, emails.offer_declined,
                     emails.rejected, emails.cancelled_by_guest, emails.cancelled_by_guide, emails.new_date_proposed,
                     emails.expired, emails.invoice_sent, emails.paid):
            func(b)
        emails.new_date_answered(b, accepted=True)
        emails.new_date_answered(b, accepted=False)
        emails.booking_changed(b, [("price", "CHF 1", "CHF 2")])
        for func in (emails.registration_confirmed, emails.registration_too_late, emails.payment_failed,
                     emails.departure_confirmed):
            func(reg)
        emails.registration_cancelled(reg, fee=Decimal("240"), percent=50)
        emails.departure_cancelled(reg, automatic=True)
        emails.goodwill_refund(reg, Decimal("10"))
        emails.decision_reminder(dep)
        emails.departure_auto_cancelled_guide(dep, 1)
    assert not log.exception.called, log.exception.call_args_list
    guest_mails = [m for m in mailoutbox if m.to == ["gast@example.com"]]
    assert guest_mails
    for m in guest_mails:
        assert settings.SITE_URL in m.body or "tourchef.test" in m.body
        assert "Hallo" not in m.body.split("\n", 1)[0], (m.subject, m.body[:80])


# --- DB-free variants (run without Postgres) --------------------------------------------


def test_nodb_receipt_uses_local_date():
    from accounts.models import User

    guide = User(display_name="Anna", email="a@example.ch")
    b = Booking(guide=guide, start_date=date(2026, 7, 1), end_date=date(2026, 7, 1), participants=1, price=100,
                amount_paid=Decimal("100"), payment_method="manual",
                paid_at=datetime(2026, 6, 30, 22, 30, tzinfo=ZoneInfo("UTC")))  # = 01.07.2026 00:30 Zurich
    from django.utils import translation

    with translation.override("de"):
        text = emails._receipt(b)
    paid_line = [line for line in text.splitlines() if line.startswith("Bezahlt am")][0]
    assert "01.07.2026" in paid_line, paid_line


def test_cancelling_departure_lists_kept_fees_for_the_guide(stripe_guide, make_departure, make_registration,
                                                            mailoutbox, django_capture_on_commit_callbacks):
    """Questionnaire Q5 (answer C): a guest cancelled late and paid a fee; then the guide calls off the date. The fee
    stays, but the guide's email lists it with a link, so the guide decides case by case (goodwill refund)."""
    dep = make_departure(stripe_guide, start=in_days(5))
    early = make_registration(dep, email="frueh@example.com")  # within 7 days -> 100 % fee
    make_registration(dep, email="bleibt@example.com")
    with mock.patch("stripe.Refund.create"), django_capture_on_commit_callbacks(execute=True):
        departures.guest_cancel(early)
    mailoutbox.clear()
    with mock.patch("stripe.Refund.create"), django_capture_on_commit_callbacks(execute=True):
        departures.cancel_departure(dep, "Wetter")
    early.refresh_from_db()
    assert early.status == S.CANCELLED and early.refundable == early.amount_paid  # fee kept for now
    guide_mail = next(m for m in mailoutbox if m.to == [stripe_guide.email])
    assert "Storno-Kosten bezahlt" in guide_mail.body and f"/dashboard/buchungen/{early.pk}/" in guide_mail.body
    assert "CHF 480.00" in guide_mail.body
