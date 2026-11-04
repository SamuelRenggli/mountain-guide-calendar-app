from django.db import migrations


class Migration(migrations.Migration):
    """Tours become templates for scheduled dates: their price is per person."""

    dependencies = [("bookings", "0005_billing")]

    operations = [migrations.RenameField("tour", "price", "price_per_person")]
