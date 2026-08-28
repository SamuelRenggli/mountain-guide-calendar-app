# 🎯 Testing Guide – Kalender Booking System Improvements

## What's New

The mountain guide booking system now supports a complete request-modification workflow:

### 🎫 Customer Flow (Widget)
1. **Browse calendar** - See available dates (green = "frei")
2. **Select dates** - Click dates to select (default 2 days, range display)
3. **Fill request form:**
   - Name (required)
   - Email (required)
   - Phone number (new)
   - Activity notes/description (new)
   - Number of days
4. **Submit** - Booking request sent to guide

### 🗂️ Guide Flow (Dashboard)
1. **View requests** - See all bookings with customer details (name, email, phone, notes)
2. **Modify if needed** - Click "🔧 Modifizieren" to:
   - Change date
   - Change price
   - Change activity/tour name
   - Add message to customer
3. **Send modification** - Customer gets email with changes
4. **Customer accepts** - Guide sees booking status change to "accepted"
5. **Mark payment** - After payment arrives, click "💳 Zahlung bestätigen"

---

## Testing Scenarios

### Scenario 1: Basic Booking Request (No Modifications)

**Steps:**
1. Open the widget calendar (iframe)
2. Click 2 green (frei) dates
3. Fill in form:
   - Name: "Test Customer"
   - Email: "test@example.com"
   - Phone: "+41 79 123 45 67"
   - Notes: "Gerne mit Kindern"
4. Click "Anfrage senden"
5. **Expected:** 
   - Modal closes
   - Customer gets confirmation email
   - Dashboard shows booking with status "pending"

---

### Scenario 2: Guide Modifies Request

**Steps:**
1. In Dashboard, find the booking from Scenario 1
2. Click "🔧 Modifizieren"
3. In modal, fill:
   - Neues Datum: (pick a different free date)
   - Neue Aktivität: "Kletterkurs"
   - Neuer Preis: "200"
   - Nachricht: "Bessere für deine Gruppe verfügbar"
4. Click "Modifikation senden"
5. **Expected:**
   - Toast: "✓ Modifikation gesendet!"
   - Booking status changes to "awaiting_modification"
   - Customer email sent with modification details
   - Dashboard shows modification fields

---

### Scenario 3: Customer Accepts Modification

**Steps:**
1. Customer receives email with modifications
2. Opens payment link or dashboard
3. Reviews changes and accepts (in real flow, this is via email link)
4. **Expected:**
   - Booking status changes to "accepted"
   - Guide gets email: "Buchung akzeptiert"
   - Booking ready for payment

---

### Scenario 4: Payment Confirmation

**Steps:**
1. After modification accepted, guide sees booking with status "accepted"
2. Guide clicks "💳 Zahlung bestätigen"
3. Confirms "Zahlung als erhalten markieren"
4. **Expected:**
   - Payment status changes to "paid"
   - Booking status changes to "gebucht"
   - Guide gets email confirmation
   - Calendar slot updates to "gebucht"

---

## Database Changes

### Booking Table - New Fields
```sql
-- Phone number from customer
guest_phone VARCHAR(20) NULL

-- Customer's activity notes
guest_notes TEXT NULL

-- Booking workflow status
booking_status ENUM('pending', 'awaiting_modification', 'modified', 'accepted', 'rejected')

-- Guide's proposed changes
guide_modified_date DATE NULL
guide_modified_price FLOAT NULL
guide_modified_tour_name VARCHAR(200) NULL
modification_sent_at DATETIME NULL
```

---

## API Endpoints - New & Updated

### POST /bookings/ (UPDATED)
**Request:**
```json
{
  "guest_name": "Test Customer",
  "guest_email": "test@example.com",
  "guest_phone": "+41 79 123 45 67",
  "guest_notes": "Gerne mit Kindern",
  "timeslot_id": 1
}
```

### POST /bookings/{booking_id}/modify (NEW)
**Request:**
```json
{
  "modified_date": "2024-09-15",
  "modified_price": 200.00,
  "modified_tour_name": "Kletterkurs",
  "message": "Bessere für deine Gruppe verfügbar"
}
```
**Response:**
```json
{
  "id": 1,
  "guest_name": "Test Customer",
  "booking_status": "awaiting_modification",
  "guide_modified_date": "2024-09-15",
  "guide_modified_price": 200.00,
  "guide_modified_tour_name": "Kletterkurs",
  "modification_sent_at": "2024-08-28T10:30:00"
}
```

### POST /bookings/{booking_id}/accept-modification (NEW)
**Response:** Updates booking_status to "accepted"

### GET /bookings/ (UPDATED)
**Now returns:** All new booking fields including phone, notes, modification status

---

## Widget Features

### Calendar Display
- **Green (frei)** - Available, clickable
- **Yellow (anfrage)** - Request pending
- **Blue (gebucht)** - Booked
- **Red (blockiert)** - Unavailable
- **Light gray (past)** - Past dates

### Date Selection
- Click dates to select (multi-select)
- Selected dates highlighted in darker green
- Dates between selection shown in lighter green (range)
- "Auswahl löschen" button to clear selection
- Selection info shows selected date range

### Request Form
- **Name** - Required
- **Email** - Required, validated
- **Phone** - Optional
- **Activity Notes** - Optional textarea
- **Days** - Auto-populated from selection, editable
- Auto-focus modal when dates selected
- Press Ctrl+Enter in notes to submit

---

## Dashboard Features

### Booking Display
**Per Booking Card Shows:**
- Guest name (bold)
- Badges: booking_status + payment_status
- Date
- Email (clickable mailto)
- Phone (if provided)
- Notes (if provided, in italic with quote marks)
- Modification details (if guide modified)

### Status-Specific Actions
- **pending/no status:** "Modifizieren" | "Bestätigen" | "Ablehnen"
- **awaiting_modification:** Shows pending changes
- **accepted:** "Zahlung bestätigen" | "Ablehnen"
- **rejected:** Shows rejection status

### Modification Modal
- Shows customer name and date
- Fields (all optional):
  - Neues Datum
  - Neue Aktivität/Tour
  - Neuer Preis CHF
  - Nachricht an Gast
- Validation: at least one change required
- Sends email automatically

---

## Status Workflow Diagram

```
Customer Creates Request
        ↓
    pending
        ↓
[Guide sees booking]
        ↓
    [Guide Modifies?]
    ↙          ↘
   NO           YES
    ↓            ↓
gebucht    awaiting_modification
           ↓
       [Customer Reviews]
           ↙           ↘
        REJECT        ACCEPT
          ↓              ↓
      rejected       accepted
                       ↓
                   [Payment]
                       ↓
                       paid
                       ↓
                    gebucht
```

---

## Email Notifications

### 1. Guest Booking Received (POST /bookings/)
- To: guest_email
- Subject: Booking confirmation
- Body: Guide name, date, status

### 2. Guide New Booking (POST /bookings/)
- To: guide_email
- Subject: New booking request
- Body: Guest name, email, date

### 3. Modification Sent (POST /bookings/{}/modify)
- To: guest_email
- Subject: "Modifizierte Buchungsanfrage von {guide_name}"
- Body: Lists each modification (date/price/tour)

### 4. Modification Accepted (POST /bookings/{}/accept-modification)
- To: guide_email
- Subject: "Buchung akzeptiert – {guest_name}"
- Body: Confirms guest accepted modifications

### 5. Payment Confirmed (Stripe webhook + mark-paid)
- To: guide_email
- Subject: "Zahlung erhalten"
- Body: Booking now confirmed

---

## Quick Test Checklist

- [ ] Customer can select multiple dates
- [ ] Widget shows date range visualization
- [ ] Phone number and notes save correctly
- [ ] Dashboard displays all booking info
- [ ] Guide can open modification modal
- [ ] Modification form validates (requires ≥1 change)
- [ ] Booking status changes to "awaiting_modification"
- [ ] Customer receives modification email
- [ ] Payment status tracking works
- [ ] Calendar reflects booking status (anfrage → gebucht)
- [ ] All email notifications send

---

## Running the App

### With Docker
```bash
cd n:\Tourchef\kalender
docker-compose up
```

### Without Docker
```bash
cd n:\Tourchef\kalender
pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Then:
- Dashboard: http://localhost:8000/dashboard/1
- Widget: http://localhost:8000/widget/1

---

## Files Modified

1. **app/models.py** - Added new Booking fields
2. **app/schemas.py** - Updated Pydantic models
3. **app/crud.py** - New functions for modification workflow
4. **app/main.py** - New API endpoints + email notifications
5. **app/templates/widget.html** - Multi-date selection + new form fields
6. **app/templates/dashboard.html** - Modification modal + new UI

---

## Backups Created
- `app/templates/widget_backup.html` - Original widget
- `app/templates/dashboard_backup.html` - Original dashboard
- `app/templates/widget_new.html` - Improved widget (now active)
- `app/templates/dashboard_new.html` - Improved dashboard (now active)

