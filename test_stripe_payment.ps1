# Test-Skript für Stripe-Zahlung
# Automatisiert den ganzen Flow: Guide -> Tour -> Timeslot -> Booking -> Checkout

param(
    [switch]$OpenCheckout = $false
)

$baseUrl = "http://localhost:8000"

Write-Host "=== Stripe Payment Flow Test ===" -ForegroundColor Cyan

# ===== SCHRITT 1: Guide erstellen =====
Write-Host "`n1. Guide erstellen..." -ForegroundColor Yellow

# Generiere zufällige E-Mail
$randomId = Get-Random -Minimum 1000 -Maximum 9999
$guideEmail = "guide$randomId@bergfuehrer.ch"

$guideBody = @{
    email = $guideEmail
    name = "Anna Müller"
    ical_url = $null
} | ConvertTo-Json

try {
    $response = Invoke-WebRequest -Uri "$baseUrl/guides/" `
        -Method POST `
        -ContentType "application/json" `
        -Body $guideBody `
        -UseBasicParsing
    
    $guide = $response.Content | ConvertFrom-Json
    $guideId = $guide.id
    Write-Host "[OK] Guide erstellt: ID $guideId - $($guide.name)" -ForegroundColor Green
} catch {
    Write-Host "[FAIL] Fehler beim Erstellen des Guides" -ForegroundColor Red
    exit 1
}

# ===== SCHRITT 2: Tour erstellen =====
Write-Host "`n2. Tour erstellen..." -ForegroundColor Yellow

$tourBody = @{
    title = "Matterhorn Besteigung"
    description = "Professionelle Besteigung des Matterhorns mit erfahrenem Guide"
    price = 150.00
    duration_hours = 8
} | ConvertTo-Json

try {
    $response = Invoke-WebRequest -Uri "$baseUrl/guides/$guideId/touren/" `
        -Method POST `
        -ContentType "application/json" `
        -Body $tourBody `
        -UseBasicParsing
    
    $tour = $response.Content | ConvertFrom-Json
    $tourId = $tour.id
    Write-Host "[OK] Tour erstellt: ID $tourId - $($tour.title) (CHF $($tour.price))" -ForegroundColor Green
} catch {
    Write-Host "[FAIL] Fehler beim Erstellen der Tour" -ForegroundColor Red
    exit 1
}

# ===== SCHRITT 3: Timeslot erstellen =====
Write-Host "`n3. Timeslot erstellen..." -ForegroundColor Yellow

# Datum 5 Tage in der Zukunft
$futureDate = (Get-Date).AddDays(5).ToString("yyyy-MM-dd")

$timeslotBody = @{
    date = $futureDate
    status = "frei"
    tour_id = $tourId
} | ConvertTo-Json

try {
    $response = Invoke-WebRequest -Uri "$baseUrl/guides/$guideId/timeslots/" `
        -Method POST `
        -ContentType "application/json" `
        -Body $timeslotBody `
        -UseBasicParsing
    
    $timeslot = $response.Content | ConvertFrom-Json
    $timeslotId = $timeslot.id
    Write-Host "[OK] Timeslot erstellt: ID $timeslotId - $($timeslot.date) (Status: $($timeslot.status))" -ForegroundColor Green
} catch {
    Write-Host "[FAIL] Fehler beim Erstellen des Timeslots" -ForegroundColor Red
    exit 1
}

# ===== SCHRITT 4: Booking erstellen =====
Write-Host "`n4. Booking erstellen..." -ForegroundColor Yellow

$bookingBody = @{
    guest_name = "Peter Schmidt"
    guest_email = "peter@example.com"
    timeslot_id = $timeslotId
    payment_status = "pending"
} | ConvertTo-Json

try {
    $response = Invoke-WebRequest -Uri "$baseUrl/bookings/" `
        -Method POST `
        -ContentType "application/json" `
        -Body $bookingBody `
        -UseBasicParsing
    
    $booking = $response.Content | ConvertFrom-Json
    $bookingId = $booking.id
    Write-Host "[OK] Booking erstellt: ID $bookingId" -ForegroundColor Green
    Write-Host "     Guest: $($booking.guest_name) ($($booking.guest_email))"
    Write-Host "     Status: $($booking.payment_status)"
} catch {
    Write-Host "[FAIL] Fehler beim Erstellen der Booking" -ForegroundColor Red
    exit 1
}

# ===== SCHRITT 5: Stripe Checkout Session erstellen =====
Write-Host "`n5. Stripe Checkout Session erstellen..." -ForegroundColor Yellow

try {
    $response = Invoke-WebRequest -Uri "$baseUrl/bookings/$bookingId/pay" `
        -Method POST `
        -UseBasicParsing
    
    $checkout = $response.Content | ConvertFrom-Json
    $checkoutUrl = $checkout.checkout_url
    
    if ($checkoutUrl) {
        Write-Host "[OK] Checkout Session erstellt!" -ForegroundColor Green
        Write-Host "     URL: $checkoutUrl" -ForegroundColor Cyan
    } else {
        Write-Host "[FAIL] Keine Checkout-URL erhalten" -ForegroundColor Red
        Write-Host $response.Content
        exit 1
    }
} catch {
    Write-Host "[FAIL] Fehler beim Erstellen der Checkout-Session" -ForegroundColor Red
    Write-Host "Details: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

# ===== SCHRITT 6: Zusammenfassung =====
Write-Host "`n" -ForegroundColor Cyan
Write-Host "=== TEST ERFOLGREICH ===" -ForegroundColor Green
Write-Host "`nZahlungstest bereit:" -ForegroundColor Green
Write-Host "  Guide ID:     $guideId" -ForegroundColor White
Write-Host "  Tour ID:      $tourId" -ForegroundColor White
Write-Host "  Timeslot ID:  $timeslotId" -ForegroundColor White
Write-Host "  Booking ID:   $bookingId" -ForegroundColor White

Write-Host "`nTest-Kartennummern:" -ForegroundColor Yellow
Write-Host "  Erfolgreich: 4242 4242 4242 4242" -ForegroundColor Green
Write-Host "  Ablehnung:   4000 0000 0000 0002" -ForegroundColor Red
Write-Host "  Beliebiges Expiry & CVC" -ForegroundColor Gray

Write-Host "`nNaechste Schritte:" -ForegroundColor Cyan
Write-Host "  1. Test-Karte eingeben (siehe oben)" -ForegroundColor Gray
Write-Host "  2. Zahlungsbestaetigungsemail erhalten" -ForegroundColor Gray
Write-Host "  3. Erfolgsseite: $baseUrl/payment/success?booking_id=$bookingId" -ForegroundColor Gray

if ($OpenCheckout) {
    Write-Host "`nOeffne Checkout URL..." -ForegroundColor Yellow
    Start-Process $checkoutUrl
} else {
    Write-Host "`n[INFO] Kopiere diese URL in deinen Browser:" -ForegroundColor Cyan
    Write-Host $checkoutUrl -ForegroundColor Yellow
}

Write-Host "`nTipps:" -ForegroundColor Cyan
Write-Host "  - Mit -OpenCheckout Flag automatisch Browser oeffnen" -ForegroundColor Gray
Write-Host "  - Logs ansehen: docker compose logs web -f" -ForegroundColor Gray
Write-Host "  - Alle Bookings: GET /bookings/" -ForegroundColor Gray
Write-Host "  - Details: siehe STRIPE_PAYMENT_GUIDE.md" -ForegroundColor Gray
