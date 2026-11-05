"""Tours the guide leads for other providers, and partner links through which providers offer such tours.

A partner sees only the periods the guide released for them and whether each day is still free; nothing about
guests, bookings or the guide's other calendar entries.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from . import availability, emails
from .models import ExternalTour, Partner

# Days a partner may not offer: something else is already there (or it's over).
_TAKEN = {availability.BUSY, availability.BLOCKED, availability.REQUESTED, availability.DEPARTURE,
          availability.EXTERNAL, availability.BOOKED, availability.PAST}


class PartnerOfferError(Exception):
    """User-facing message."""


def released_days(partner: Partner, start: date, end: date) -> set[date]:
    days = set()
    for s, e in partner.periods.filter(start_date__lte=end, end_date__gte=start).values_list("start_date", "end_date"):
        days.update(availability.days_between(max(s, start), min(e, end)))
    return days


def free_days(partner: Partner, start: date, end: date) -> set[date]:
    """Released days in start..end on which the guide has nothing else."""
    released = released_days(partner, start, end)
    if not released:
        return set()
    states = availability.day_states(partner.guide, start, end)
    return {d for d in released if states[d] not in _TAKEN}


def create_offer(partner: Partner, *, title, url, start_date, end_date, contact_name, contact_email, note,
                 language) -> ExternalTour:
    if start_date <= timezone.localdate():
        raise PartnerOfferError(_("Die Tour muss in der Zukunft liegen."))
    with transaction.atomic():
        # Serialise offers for this guide so two partners can't take the same days.
        get_user_model().objects.select_for_update().filter(pk=partner.guide_id).first()
        days = set(availability.days_between(start_date, end_date))
        if not days <= released_days(partner, start_date, end_date):
            raise PartnerOfferError(_("Diese Tage sind nicht für dich freigegeben."))
        if not days <= free_days(partner, start_date, end_date):
            raise PartnerOfferError(_("Mindestens ein Tag ist nicht mehr frei."))
        tour = ExternalTour.objects.create(
            guide=partner.guide, partner=partner, provider=partner.name, title=title, url=url,
            start_date=start_date, end_date=end_date, contact_name=contact_name, contact_email=contact_email,
            note=note, language=language, status=ExternalTour.Status.PENDING,
            show_to_guests=False,  # the guide decides whether guests see a partner's tour
        )
    transaction.on_commit(lambda: emails.partner_offer_to_guide(tour))
    return tour


def answer_offer(tour: ExternalTour, *, accept: bool) -> ExternalTour:
    with transaction.atomic():
        locked = ExternalTour.objects.select_for_update().get(pk=tour.pk)
        if not locked.is_pending:
            raise PartnerOfferError(_("Dieses Angebot wurde bereits beantwortet."))
        locked.status = ExternalTour.Status.CONFIRMED if accept else ExternalTour.Status.DECLINED
        locked.answered_at = timezone.now()
        locked.save(update_fields=["status", "answered_at"])
    transaction.on_commit(lambda: emails.partner_offer_answered(locked, accepted=accept))
    return locked
