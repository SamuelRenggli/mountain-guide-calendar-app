# Run App

This project runs with Docker Compose.

## Prerequisites

- Docker Desktop installed and running
- A filled-in `.env` file at the project root

If you do not already have a local `.env`, create one from the example:

```bash
copy .env.example .env
```

Then fill in the required values, especially `MYSQL_ROOT_PASSWORD`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `STRIPE_SECRET_KEY`, and `STRIPE_WEBHOOK_SECRET`.

## Start the application

From the project root (`n:\Tourchef\kalender`), run:

```bash
docker compose up --build
```

This will:

1. Start MySQL 8 as the `db` service
2. Build the Python image for the `web` service
3. Start FastAPI with reload enabled on port `8000`

## Useful commands

Watch the API logs:

```bash
docker compose logs -f web
```

Restart only the API after changing environment variables or code:

```bash
docker compose restart web
```

Stop the stack:

```bash
docker compose down
```

Stop the stack and remove the database volume:

```bash
docker compose down -v
```

If MySQL starts but the web container shows `Access denied for user 'root'`, run the same volume reset and start again:

```bash
docker compose down -v
docker compose up --build
```

## Open the app

After startup, open:

- `http://localhost:8000/docs` for the interactive API docs
- `http://localhost:8000/widget/{id}` for the public booking widget
- `http://localhost:8000/dashboard/{id}` for the guide dashboard
