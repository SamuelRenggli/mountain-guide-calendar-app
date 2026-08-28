# Stripe Zahlung Test - Tourchef Kalender

## Schritt 1: Stripe Test-Credentials konfigurieren

Öffne die `.env` Datei und aktualisiere die Stripe-Werte:

```
# Stripe Test-Keys (von https://dashboard.stripe.com/test/apikeys)
STRIPE_SECRET_KEY=sk_test_4eC39HqLyjWDarhtT657m89w
STRIPE_WEBHOOK_SECRET=whsec_test_1234567890abcdef

# ODER deine echten Test-Keys:
STRIPE_SECRET_KEY=sk_test_YOUR_SECRET_KEY_HERE
STRIPE_WEBHOOK_SECRET=whsec_YOUR_WEBHOOK_SECRET_HERE
```

Danach: `docker compose restart web`

---

## Schritt 2: Test-Zahlung durchführen

### Option A: Interaktive API-Dokumentation (Empfohlen!)

1. Öffne: http://localhost:8000/docs
2. Scrolle zu "POST /bookings/" → "Try it out"
3. Erstelle eine Buchung:
```json
{
  "guest_name": "Max Mustermann",
  "guest_email": "max@example.com",
  "timeslot_id": 1,
  "payment_status": "pending"
}
```
4. Bekommst du die `booking_id`
5. Gehe zu "POST /bookings/{booking_id}/pay" 
6. Nutze diese `booking_id` → "Try it out" → "Execute"
7. Du bekommst eine `checkout_url` von Stripe!

### Option B: PowerShell-Befehl

```powershell
# 1. Buchung erstellen
$bookingBody = @{
    guest_name = "Max Mustermann"
    guest_email = "max@example.com"
    timeslot_id = 1
    payment_status = "pending"
} | ConvertTo-Json

$response = Invoke-WebRequest -Uri "http://localhost:8000/bookings/" `
    -Method POST `
    -ContentType "application/json" `
    -Body $bookingBody `
    -UseBasicParsing

$booking = $response.Content | ConvertFrom-Json
$bookingId = $booking.id

Write-Host "Buchung erstellt mit ID: $bookingId"

# 2. Stripe Checkout Session erstellen
$checkoutResponse = Invoke-WebRequest -Uri "http://localhost:8000/bookings/$bookingId/pay" `
    -Method POST `
    -UseBasicParsing

$checkout = $checkoutResponse.Content | ConvertFrom-Json
Write-Host "Checkout URL: $($checkout.checkout_url)"

# 3. Öffne die URL im Browser
Start-Process $checkout.checkout_url
```

---

## Schritt 3: Stripe Test-Karten verwenden

Bei Stripe Checkout wird dir ein Zahlungsformular angezeigt. Nutze diese **Test-Kartennummern**:

### Erfolgreiche Zahlungen:
| Kartennummer | Expiry | CVC | Ergebnis |
|---|---|---|---|
| `4242 4242 4242 4242` | Beliebig (zB 12/26) | Beliebig (zB 123) | ✓ Zahlung erfolgreich |
| `5555 5555 5555 4444` | Beliebig | Beliebig | ✓ Mastercard OK |
| `2720 9999 7720 9999` | Beliebig | Beliebig | ✓ TWINT OK (falls aktiviert) |

### Fehlgeschlagene Zahlungen (zum Testen):
| Kartennummer | Fehler |
|---|---|
| `4000 0000 0000 0002` | Generic decline |
| `4000 0000 0000 0069` | Expired card |
| `4000 0000 0000 0127` | Incorrect CVC |

---

## Schritt 4: Zahlung überprüfen

### In der API-Dokumentation:
1. Gehe zu GET `/bookings/`
2. Suche die Buchung (sollte `payment_status: "paid"` sein)
3. Öffne: `http://localhost:8000/payment/success?booking_id=1`

### Oder im Dashboard (wenn vorhanden):
```
http://localhost:8000/dashboard/1
```

---

## Schritt 5: Webhook testen (Optional)

Der Webhook wird automatisch von Stripe aufgerufen wenn Zahlung erfolgreich ist. 

Zum manuellen Testen (ohne echten Webhook):

```bash
# Manuell Zahlung als bezahlt markieren
POST http://localhost:8000/bookings/{booking_id}/mark-paid
```

---

## Übersicht der Payment-Endpoints

| Endpoint | Methode | Beschreibung |
|----------|---------|---|
| `/bookings/` | POST | Neue Buchung erstellen |
| `/bookings/{id}/pay` | POST | Stripe Checkout Session erstellen |
| `/bookings/{id}/mark-paid` | POST | Manuell als bezahlt markieren |
| `/bookings/{id}/reject` | POST | Buchung ablehnen |
| `/bookings/` | GET | Alle Buchungen abrufen |
| `/stripe/webhook` | POST | Stripe Webhook (automatisch) |
| `/payment/success` | GET | Erfolgsseite nach Zahlung |
| `/payment/cancel` | GET | Abbruchseite |

---

## Full Test-Szenario

```
1. Guide erstellen
   POST /guides/ → erhält guide_id

2. Tour für Guide erstellen
   POST /guides/{guide_id}/touren/ → erhält tour_id

3. Timeslot für Tour erstellen
   POST /guides/{guide_id}/timeslots/ → erhält timeslot_id

4. Booking erstellen
   POST /bookings/ (mit timeslot_id) → erhält booking_id

5. Zahlung initiieren
   POST /bookings/{booking_id}/pay → erhält checkout_url

6. Stripe Checkout öffnen
   Browser: checkout_url

7. Test-Karte eingeben
   4242 4242 4242 4242 | 12/26 | 123

8. Zahlung bestätigen
   → payment_status wird "paid"

9. Erfolgsseite besuchen
   GET /payment/success?booking_id={booking_id}
```

---

## Debugging

### Problem: "Stripe nicht konfiguriert"
**Lösung**: 
- Überprüfe dass `STRIPE_SECRET_KEY` in `.env` gesetzt ist
- Es sollte mit `sk_test_` beginnen
- Starte neu: `docker compose restart web`

### Problem: Fehler im Logs
```bash
docker compose logs web -f
```

### Problem: Test-Zahlung wird nicht abgeschlossen
1. Öffne Browser-Entwicklertools (F12)
2. Überprüfe Console auf Fehler
3. Stelle sicher dass JavaScript aktiviert ist

---

## Nächste Schritte

1. **Echte Stripe-Credentials** 
   - Registriere dich bei https://stripe.com
   - Kopiere echte Test-Keys in `.env`

2. **Email-Benachrichtigungen**
   - Konfiguriere Mail-Credentials
   - Buchungsbestätigungen per E-Mail

3. **Webhook-URL**
   - Stripe Dashboard → Webhooks
   - Endpoint hinzufügen: `https://dein-domain.ch/stripe/webhook`
   - Events: `checkout.session.completed`

4. **Production Setup**
   - Wechsle zu echten Stripe-Keys (`sk_live_...`)
   - Aktualisiere APP_BASE_URL zu deiner Domain
   - Teste umfassend vor Launch!

---

**Status**: ✓ Stripe-Integration bereit zum Testen
