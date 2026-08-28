# 🚀 Quick Start Guide

## Installation & Setup

### Prerequisites
- Python 3.11+
- Docker & Docker Compose (optional, recommended)
- MySQL database (Docker handles this)

### Quick Start with Docker

```bash
# Navigate to project directory
cd n:\Tourchef\kalender

# Start the application
docker-compose up

# Wait for "Uvicorn running on 0.0.0.0:8000"
```

### Manual Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Set up environment variables (optional)
# Create .env file with database credentials if needed

# Run the application
python -m uvicorn app.main:app --reload
```

---

## Access the Application

Once running, open in your browser:

### 🎫 **Customer Widget** (embed on your website)
```
http://localhost:8000/widget/1
```
This is the booking calendar customers see. Embed via iframe:
```html
<iframe src="http://localhost:8000/widget/1" width="100%" height="500px" frameborder="0"></iframe>
```

### 📊 **Guide Dashboard** (manage bookings)
```
http://localhost:8000/dashboard/1
```
This is where YOU (the guide) manage bookings and modifications.

---

## First Time Setup

### 1. Create Your Profile as a Guide

```bash
# Make a POST request (using curl or API client):
curl -X POST "http://localhost:8000/guides/" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Your Name",
    "email": "your@email.com",
    "ical_url": null
  }'

# Response will include your guide_id (e.g., 1)
```

Or using Python:
```python
import requests

guide = requests.post(
    "http://localhost:8000/guides/",
    json={
        "name": "Your Name",
        "email": "your@email.com",
        "ical_url": None
    }
).json()

print(f"Your Guide ID: {guide['id']}")
```

### 2. Create Some Tours/Activities

```bash
curl -X POST "http://localhost:8000/guides/1/touren/" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Bergtour",
    "description": "Ein Tag in den Bergen",
    "price": 150.00,
    "max_participants": 8
  }'
```

### 3. Add Available Dates

**Option A: Manual dates**
- Go to Dashboard → Calendar section
- Pick a date → Select "Verfügbar (frei)" → Click "+ Hinzufügen"

**Option B: API**
```bash
curl -X POST "http://localhost:8000/guides/1/timeslots/" \
  -H "Content-Type: application/json" \
  -d '{
    "date": "2024-09-10",
    "status": "frei"
  }'
```

### 4. Share Widget Link

Embed the widget on your website:
```html
<iframe src="http://localhost:8000/widget/1" width="100%" height="600px" frameborder="0"></iframe>
```

---

## Test the Booking Flow

### As a Customer:

1. **Open Widget** → http://localhost:8000/widget/1
2. **Click 2 dates** (should turn green/highlighted)
3. **Fill in form:**
   - Name: "Test Guest"
   - Email: "guest@example.com"
   - Phone: "+41 79 123 45 67" (optional)
   - Notizen: "Interessiert in Kletterkurs"
4. **Submit** → "✓ Anfrage gesendet!"

### As the Guide:

1. **Open Dashboard** → http://localhost:8000/dashboard/1
2. **See the booking** in "Buchungsanfragen" section
3. **Review customer details:**
   - Name, Email, Phone
   - Requested dates
   - Activity notes
4. **Option A: Direct confirmation**
   - Click "✓ Bestätigen"
   - Booking ready for payment
5. **Option B: Modify request**
   - Click "🔧 Modifizieren"
   - Change date, price, or activity
   - Add message to customer
   - Click "Modifikation senden"
6. **After customer accepts:**
   - Click "💳 Zahlung bestätigen" when payment arrives
   - Booking status → "paid" ✅

---

## Common Tasks

### 🔧 Change a Booking
1. Dashboard → Find booking
2. Click "🔧 Modifizieren"
3. Change date/price/activity
4. Send modification
5. Customer gets email with details

### 📅 Block a Date (Busy)
1. Dashboard → Calendar
2. Pick date
3. Select "Blockiert"
4. Click "+ Hinzufügen"

### 🔄 Sync Google Calendar
1. Dashboard → "🔄 Google Kalender sync"
2. Must have ical_url set in your profile first
3. Private events automatically blocked

### 💳 Confirm Payment
1. When customer pays (manually or via Stripe)
2. Click "💳 Zahlung bestätigen"
3. Status updates to "paid" / "gebucht"

---

## Environment Variables (.env)

Optional configuration:

```env
# Database
DATABASE_URL=mysql://user:pass@localhost/kalender

# Email (if using email notifications)
MAIL_USERNAME=your_email@gmail.com
MAIL_PASSWORD=your_app_password

# Stripe
STRIPE_SECRET_KEY=sk_test_xxxx
STRIPE_PUBLIC_KEY=pk_test_xxxx
STRIPE_WEBHOOK_SECRET=whsec_xxxx

# App
APP_BASE_URL=http://localhost:8000
```

---

## Troubleshooting

### "Module not found" errors
```bash
# Reinstall dependencies
pip install -r requirements.txt --force-reinstall
```

### Database connection error
```bash
# If using Docker:
docker-compose down
docker-compose up --build

# If manual setup, ensure MySQL is running
```

### Widget not loading
- Check browser console (F12) for errors
- Verify guide_id exists: http://localhost:8000/guides/1
- Check CORS headers if embedding on different domain

### Emails not sending
- Configure MAIL_* env variables
- Check email_py file for SMTP settings
- May need app-specific password for Gmail

---

## API Quick Reference

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | /guides/ | Create new guide account |
| GET | /guides/{id} | Get guide profile |
| POST | /guides/{id}/touren/ | Create tour/activity |
| GET | /guides/{id}/touren/ | List tours |
| POST | /guides/{id}/timeslots/ | Create available date |
| GET | /guides/{id}/timeslots/ | List dates |
| POST | /bookings/ | Customer creates request |
| GET | /bookings/ | List all bookings |
| POST | /bookings/{id}/modify | Guide modifies booking |
| POST | /bookings/{id}/accept-modification | Customer accepts |
| POST | /bookings/{id}/mark-paid | Mark payment received |
| POST | /bookings/{id}/reject | Reject booking |

---

## What's Different Now? (vs. Original)

### Before:
- Only approve/reject bookings
- No customer details stored
- Simple yes/no workflow
- Customer couldn't negotiate

### After:
- ✅ Guide can modify (date/price/activity)
- ✅ Customer can see and accept modifications
- ✅ Store phone + activity notes
- ✅ Better communication workflow
- ✅ Professional modification process
- ✅ Email notifications at each step

---

## Next Steps

1. ✅ Set up guide profile (see "First Time Setup" above)
2. ✅ Add tours/activities
3. ✅ Add available dates
4. ✅ Share widget on your website
5. ✅ Test the booking flow
6. 🎉 Go live!

---

## Support & Testing

- **Full Testing Guide:** See [TESTING_GUIDE.md](TESTING_GUIDE.md)
- **Implementation Details:** See [IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md)
- **API Documentation:** Use FastAPI docs at http://localhost:8000/docs

---

**Ready to get started? Open http://localhost:8000/dashboard/1 now! 🚀**
