from django.db import migrations
from django.db.models import TextField
from django.db.models.functions import Left


def encrypt_existing(apps, schema_editor):
    """Phone numbers stored before 0007 are plain text. Saving them through the field encrypts them."""
    from core.fields import PREFIX

    Booking = apps.get_model("bookings", "Booking")
    # Explicit TextField: compare the raw column, not through the encrypting field.
    head = Left("guest_phone", len(PREFIX), output_field=TextField())
    plain = Booking.objects.exclude(guest_phone="").annotate(head=head).exclude(head=PREFIX)
    for pk, phone in plain.values_list("pk", "guest_phone").iterator():
        Booking.objects.filter(pk=pk).update(guest_phone=phone)  # get_prep_value encrypts


class Migration(migrations.Migration):
    dependencies = [("bookings", "0007_departures")]

    operations = [migrations.RunPython(encrypt_existing, migrations.RunPython.noop)]
