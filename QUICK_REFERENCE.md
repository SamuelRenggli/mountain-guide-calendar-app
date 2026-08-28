# Quick Reference Guide - Tourchef Kalender

## Application Status
✓ **Running Successfully**  
✓ **All Tests Passed**  
✓ **Ready to Use**

---

## Quick Start

### Application is Already Running!
The app is running in Docker. You can access it immediately:

**Interactive API Documentation:**
```
http://localhost:8000/docs
```

**Example URLs:**
- Widget: http://localhost:8000/widget/2
- Dashboard: http://localhost:8000/dashboard/2
- API Docs: http://localhost:8000/docs

---

## Important Commands

### View Logs
```bash
# View FastAPI logs
docker compose logs web -f

# View Database logs
docker compose logs db -f

# View both
docker compose logs -f
```

### Restart Services
```bash
# Restart just the API (quick)
docker compose restart web

# Restart everything
docker compose restart

# Stop everything
docker compose down

# Stop and remove database (RESET)
docker compose down -v
```

### Start Fresh
```bash
# After stopping, restart with fresh database
docker compose up --build
```

---

## Testing the API

### Using the Interactive Docs
1. Open: http://localhost:8000/docs
2. Click "Try it out" on any endpoint
3. Fill in required fields
4. Click "Execute"

### Using PowerShell
```powershell
# Create a guide
$body = '{"email":"test@example.com","name":"Test User"}'
Invoke-WebRequest -Uri "http://localhost:8000/guides/" `
    -Method POST `
    -ContentType "application/json" `
    -Body $body
```

---

## Database Access

### Connect to MySQL
```bash
docker exec -it bf_mysql_db mysql -u tourchef_app -p bergfuehrer_db
# Password: test_app_password_123

# Common queries:
# SELECT * FROM user;
# SELECT * FROM tour;
# SELECT * FROM timeslot;
```

---

## Environment Configuration

**Location**: `.env`

**Key Variables:**
- `MYSQL_DATABASE` - Database name
- `MYSQL_USER` / `MYSQL_PASSWORD` - App user credentials
- `APP_BASE_URL` - Application URL
- `STRIPE_SECRET_KEY` - Stripe API key
- `MAIL_USERNAME` / `MAIL_PASSWORD` - Email credentials

**To update:**
1. Edit `.env` file
2. Run `docker compose restart web` (for app changes)
3. Run `docker compose restart db` (for database changes)

---

## Common Tasks

### Add a Test Guide
```powershell
powershell -File test_api.ps1
```

### View Current Guides
```
GET http://localhost:8000/guides/
```

### Create a Tour for a Guide
```
POST http://localhost:8000/guides/{guide_id}/touren/
Content-Type: application/json

{
  "title": "Matterhorn Besteigung",
  "description": "Geführte Besteigung des Matterhorns",
  "price": 150.00,
  "duration_hours": 8
}
```

### Create a Timeslot
```
POST http://localhost:8000/guides/{guide_id}/timeslots/
Content-Type: application/json

{
  "date": "2026-08-15",
  "status": "available",
  "tour_id": 1
}
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Can't access http://localhost:8000 | Check if containers are running: `docker compose ps` |
| Database error | Run: `docker compose down -v && docker compose up --build` |
| Port 8000 in use | Change port in `docker-compose.yml` or kill process on port 8000 |
| Can't create user | Email might already exist; use unique email addresses |
| Logs too verbose | Use: `docker compose logs --tail 10` |

---

## Useful Files

- **Main Application**: `app/main.py`
- **Database Models**: `app/models.py`
- **API Schemas**: `app/schemas.py`
- **Database Config**: `app/database.py`
- **CRUD Operations**: `app/crud.py`
- **Templates**: `app/templates/` (widget.html, dashboard.html)
- **Configuration**: `docker-compose.yml`
- **Dependencies**: `requirements.txt`

---

## Next Development Steps

1. **Customize Templates**
   - Edit `app/templates/widget.html` for booking widget
   - Edit `app/templates/dashboard.html` for guide dashboard

2. **Add More Endpoints**
   - See `app/main.py` for endpoint patterns
   - Add new endpoints after the existing ones

3. **Database Migrations**
   - Add new models in `app/models.py`
   - Models are auto-created on startup via SQLAlchemy

4. **Email Testing**
   - Update `MAIL_USERNAME` and `MAIL_PASSWORD` in `.env`
   - Edit `app/email.py` to customize email templates

5. **Stripe Integration**
   - Add real `STRIPE_SECRET_KEY` in `.env`
   - Add webhook handling in `app/main.py`

---

## Support Information

**Container Names:**
- `bf_fastapi_backend` - FastAPI application
- `bf_mysql_db` - MySQL database

**Network:** `kalender_default`  
**Volumes:** `kalender_mysql_data`

**Framework**: FastAPI with SQLAlchemy ORM  
**Database**: MySQL 8.0  
**Python**: 3.11  

---

**Application Ready!** 🚀
