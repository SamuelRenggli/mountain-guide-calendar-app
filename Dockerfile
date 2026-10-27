FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN SECRET_KEY=build-only python manage.py collectstatic --noinput \
    && useradd --create-home --uid 1000 app \
    && chown -R app /app
USER app

EXPOSE 8000
# PORT is set by Cloud Run. There, migrations run as a separate job before each deploy (RUN_MIGRATIONS=0).
CMD ["sh", "-c", "if [ \"${RUN_MIGRATIONS:-1}\" = 1 ]; then python manage.py migrate --noinput && python manage.py createcachetable; fi && exec gunicorn config.wsgi --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-3} --timeout 60 --access-logfile -"]
