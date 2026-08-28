# Stripe Setup - Echte Test-Credentials Configurieren

## Problem
Der aktuelle Stripe Key ist ungültig. Das ist ein Dummy-Key. Du brauchst **echte Stripe Test-Keys** zum Testen.

---

## Schritt-für-Schritt Guide

### 1. Stripe Account erstellen

1. Gehe zu: https://stripe.com
2. Klicke auf **"Start now"** oder **"Sign up"**
3. Melde dich mit E-Mail an:
   - E-Mail: deine@email.com
   - Passwort: sicheres Passwort
4. Fülle die Account-Informationen aus:
   - Business name: "Tourchef Kalender"
   - Land: Switzerland
5. Bestätige E-Mail
6. Fertig! ✓

### 2. Test-Keys abrufen

1. Nach dem Login zum **Stripe Dashboard**: https://dashboard.stripe.com
2. Stelle sicher, dass du im **"Test mode"** bist (Toggle oben rechts)
3. Gehe zu: **Developers** → **API Keys** (Sidebar)
4. Du siehst:
   - **Publishable key** (z.B.: `pk_test_...`)
   - **Secret key** (z.B.: `sk_test_...`) ← **DIESE brauchst du!**
5. Kopiere den **Secret key** (mit dem Kopier-Icon)

### 3. Secret Key in .env einfügen

1. Öffne deine `.env` Datei im VS Code
2. Finde die Zeile:
   ```
   STRIPE_SECRET_KEY=sk_test_...
   ```
3. Ersetze sie mit deinem echten Key:
   ```
   STRIPE_SECRET_KEY=sk_test_DEIN_ECHTER_KEY_HIER
   ```
4. Speichern (Ctrl+S)

### 4. Webhook-Secret konfigurieren (Optional, für Production)

Für jetzt kannst du dies ignorieren. Später für Production:

1. Dashboard → **Developers** → **Webhooks**
2. Klicke **"Add an endpoint"**
3. Endpoint URL: `https://dein-domain.ch/stripe/webhook`
4. Events: Wähle `checkout.session.completed`
5. Kopiere das **Signing secret** (whsec_...)
6. Füge es in `.env` ein:
   ```
   STRIPE_WEBHOOK_SECRET=whsec_DEIN_WEBHOOK_SECRET
   ```

### 5. Docker Container neu starten

```bash
docker compose restart web
```

Warte 5-10 Sekunden bis der Container neu gestartet ist.

---

## Test durchführen

### Option A: Automatisiertes Skript

```powershell
cd n:\Tourchef\kalender
powershell -File test_stripe_payment.ps1
```

Das Skript wird:
1. Guide erstellen
2. Tour erstellen
3. Timeslot erstellen
4. Booking erstellen
5. **Stripe Checkout-URL generieren**

### Option B: Interaktive Dokumentation

1. Öffne: http://localhost:8000/docs
2. Scroll zu **POST /bookings/**
3. Erstelle eine Buchung (mit einem echten timeslot_id)
4. Kopiere die `booking_id`
5. Gehe zu **POST /bookings/{booking_id}/pay**
6. Klicke **"Try it out"** → **"Execute"**
7. Du bekommst die Checkout-URL

---

## Test-Kartennummern

Bei der Zahlung in Stripe Checkout verwende diese **Test-Kartennummern**:

### ✓ Erfolgreiche Zahlungen
| Kartennummer | Expiry | CVC | Beschreibung |
|---|---|---|---|
| `4242 4242 4242 4242` | Beliebig (z.B. 12/26) | Beliebig (z.B. 123) | Standard Visa - immer erfolgreich |
| `5555 5555 5555 4444` | Beliebig | Beliebig | Mastercard - immer erfolgreich |

### ✗ Fehlgeschlagene Zahlungen (zum Testen)
| Kartennummer | Fehler |
|---|---|
| `4000 0000 0000 0002` | Zahlung wird abgelehnt |
| `4000 0000 0000 0069` | Abgelaufene Karte |
| `4000 0000 0000 0127` | Falscher CVC |
| `4000 0000 0000 0341` | 3D Secure erforderlich |

### Extra Infos
- **Expiry**: Beliebiges Datum in der Zukunft (z.B. 12/25, 06/27)
- **CVC**: Beliebige 3 oder 4 Ziffern (z.B. 123, 1234)
- **Name**: Beliebiger Name (z.B. Test User)
- **Adresse**: Beliebige Adresse (z.B. 123 Test St)

---

## Ablauf beim Testen

```
1. test_stripe_payment.ps1 ausführen
   ↓
2. Checkout-URL in Browser öffnen
   ↓
3. Test-Kartendaten eingeben (4242 4242 4242 4242)
   ↓
4. "Pay" klicken
   ↓
5. Success-Seite öffnen: http://localhost:8000/payment/success?booking_id=X
   ↓
6. Email-Benachrichtigungen prüfen (falls konfiguriert)
```

---

## Stripe Dashboard überprüfen

Nach erfolgreicher Zahlung:

1. Gehe zu: https://dashboard.stripe.com
2. Stelle sicher: **Test Mode** ist aktiviert
3. **Payments** (Sidebar) → **Payments**
4. Du solltest deine Test-Zahlung sehen mit:
   - Status: **Succeeded**
   - Amount: **CHF 150.00** (oder dein Betrag)
   - Datum: Heute

---

## Debugging

### Problem: "Stripe nicht konfiguriert"
```
Error: Stripe nicht konfiguriert
```

**Lösung:**
- Überprüfe dass `STRIPE_SECRET_KEY` in `.env` gesetzt ist
- Es darf NICHT mit `sk_test_your` beginnen
- Muss mit echtem `sk_test_...` beginnen
- Führe `docker compose restart web` aus

### Problem: "Invalid API Key"
```
stripe._error.AuthenticationError: Invalid API Key provided
```

**Lösungen:**
1. Überprüfe dass der Key korrekt kopiert wurde (keine Leerzeichen)
2. Überprüfe dass du den **Secret key** (nicht Publishable key) verwendest
3. Überprüfe dass du im **Test Mode** bist (nicht Live Mode!)
4. Generiere neue Keys: https://dashboard.stripe.com/apikeys

### Problem: Checkout-Fehler
**Fehler**: 500 Internal Server Error beim `/bookings/{id}/pay`

**Lösung:**
```bash
docker compose logs web -f | Select-String "stripe"
```

Dies zeigt den genauen Stripe-Fehler.

---

## Was ist Payment Status?

Die Buchung hat einen `payment_status`:

- **pending** - Zahlung erwartet
- **paid** - Zahlung eingegangen ✓
- **failed** - Zahlung fehlgeschlagen ✗

Du kannst auch manuell markieren:
```
POST /bookings/{booking_id}/mark-paid
```

---

## Production Setup (Später!)

Wenn du live gehst:

1. **Wechsel zu Live-Keys**:
   ```
   STRIPE_SECRET_KEY=sk_live_YOUR_LIVE_KEY
   STRIPE_WEBHOOK_SECRET=whsec_YOUR_LIVE_WEBHOOK_SECRET
   ```

2. **Webhook konfigurieren**:
   - Endpoint: `https://dein-domain.ch/stripe/webhook`
   - Signe Events mit Webhook-Secret

3. **Aktualisiere URLs**:
   ```
   APP_BASE_URL=https://dein-domain.ch
   ```

4. **WICHTIG**: Teste nochmal mit Test-Keys bevor du Live gehst!

---

## Links

- **Stripe Dashboard**: https://dashboard.stripe.com
- **Stripe Docs**: https://stripe.com/docs
- **Test Data**: https://stripe.com/docs/testing
- **Account Settings**: https://dashboard.stripe.com/settings/account

---

**Bereit zum Testen!** 🎉
