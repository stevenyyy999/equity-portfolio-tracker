# Docker Development

From a clean clone, create local environment values if you need optional integrations:

```bash
cd equity_tracker
cp .env.example .env
```

Then start the stack:

```bash
docker compose up --build
```

The compose stack starts Postgres, waits until it is reachable, runs Django migrations, loads stock CSV seed data through migrations, and starts the Django development server at http://localhost:8000.

The Stripe webhook listener starts only when `STRIPE_SECRET_KEY` is set. Google OAuth requires `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`; without them, normal email/password auth can still run.

Useful commands:

```bash
docker compose exec web python manage.py test
docker compose exec web python manage.py createsuperuser
docker compose down
docker compose down --volumes
```
