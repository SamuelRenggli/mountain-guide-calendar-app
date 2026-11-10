#!/bin/sh
# Runs as the Cloud Run job "tourchef-migrate" before every deploy.
set -e
python manage.py migrate --noinput
python manage.py createcachetable
python manage.py load_stock_pictures
