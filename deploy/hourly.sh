#!/bin/sh
# Runs as the Cloud Run job "tourchef-hourly", started every hour by Cloud Scheduler.
python manage.py expire_bookings
python manage.py sync_calendars
