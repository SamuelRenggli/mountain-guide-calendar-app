from fastapi import FastAPI, Depends, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError
from contextlib import asynccontextmanager
import os
import asyncio
import datetime
import time
import requests as http_requests
from icalendar import Calendar

from .database import engine, Base, get_db, SessionLocal
from . import models, schemas, crud
from .email import notify_guide_new_booking, notify_guest_booking_received

import stripe
import os
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000")


def _wait_for_database(max_attempts: int = 30, initial_delay: float = 1.0) -> None:
    """Wait until the database accepts connections and schema creation succeeds."""
    for attempt in range(1, max_attempts + 1):
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql("SELECT 1")
            Base.metadata.create_all(bind=engine)
            return
        except OperationalError as exc:
            if attempt == max_attempts:
                raise
            delay = min(initial_delay * attempt, 5.0)
            print(f"[startup] Waiting for database ({attempt}/{max_attempts}): {exc}")
            time.sleep(delay)


def _sync_all_guides():
    """Fetch all guides with an ical_url and block their busy dates."""
    db = SessionLocal()
    try:
        guides = db.query(models.User).filter(models.User.ical_url.isnot(None)).all()
        for guide in guides:
            try:
                resp = http_requests.get(guide.ical_url, timeout=10)
                resp.raise_for_status()
                cal = Calendar.from_ical(resp.content)
                today = datetime.date.today()
                for component in cal.walk():
                    if component.name != "VEVENT":
                        continue
                    dtstart = component.get("DTSTART")
                    dtend = component.get("DTEND")
                    if not dtstart:
                        continue
                    start = dtstart.dt
                    end = dtend.dt if dtend else start
                    if hasattr(start, "date"):
                        start = start.date()
                    if hasattr(end, "date"):
                        end = end.date()
                    current = start
                    while current < end:
                        if current >= today:
                            crud.upsert_blocked_timeslot(db, guide.id, current)
                        current += datetime.timedelta(days=1)
            except Exception as e:
                print(f"[sync] Guide {guide.id} failed: {e}")
    finally:
        db.close()


async def _auto_sync_loop():
    while True:
        print("[sync] Running Google Calendar auto-sync...")
        _sync_all_guides()
        print("[sync] Done. Next sync in 6 hours.")
        await asyncio.sleep(6 * 60 * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _wait_for_database()
    asyncio.create_task(_auto_sync_loop())
    yield


app = FastAPI(title="Bergführer Buchungs-API", version="0.1.0", lifespan=lifespan)

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


# ---------------------------------------------------------------------------
# Widget endpoint (public — embedded via iframe on any website)
# ---------------------------------------------------------------------------

@app.get("/widget/{guide_id}", response_class=HTMLResponse, summary="Widget-Kalender für einen Bergführer")
def get_widget(request: Request, guide_id: int, db: Session = Depends(get_db)):
    guide = crud.get_user(db, guide_id)
    if not guide:
        raise HTTPException(status_code=404, detail="Bergführer nicht gefunden")
    return templates.TemplateResponse("widget.html", {
        "request": request,
        "guide_id": guide_id,
        "guide_name": guide.name,
    })


@app.get("/widget/{guide_id}/data", summary="Widget-JSON-Daten für einen Bergführer")
def get_widget_data(guide_id: int, db: Session = Depends(get_db)):
    guide = crud.get_user(db, guide_id)
    if not guide:
        raise HTTPException(status_code=404, detail="Bergführer nicht gefunden")
    timeslots = crud.get_timeslots_for_guide(db, guide_id)
    return {
        "guide_name": guide.name,
        "touren": [{"id": t.id, "title": t.title, "price": t.price} for t in guide.touren],
        "timeslots": [
            {"id": s.id, "date": s.date.isoformat(), "status": s.status, "tour_id": s.tour_id}
            for s in timeslots
        ],
    }


# ---------------------------------------------------------------------------
# Guides
# ---------------------------------------------------------------------------

@app.post("/guides/", response_model=schemas.UserResponse, status_code=201, summary="Bergführer anlegen")
def create_guide(user: schemas.UserCreate, db: Session = Depends(get_db)):
    if crud.get_user_by_email(db, user.email):
        raise HTTPException(status_code=400, detail="E-Mail bereits registriert")
    return crud.create_user(db, user)


@app.get("/guides/{guide_id}", response_model=schemas.UserResponse, summary="Bergführer abrufen")
def get_guide(guide_id: int, db: Session = Depends(get_db)):
    guide = crud.get_user(db, guide_id)
    if not guide:
        raise HTTPException(status_code=404, detail="Bergführer nicht gefunden")
    return guide


@app.patch("/guides/{guide_id}", response_model=schemas.UserResponse, summary="Bergführer aktualisieren")
def update_guide(guide_id: int, data: schemas.UserUpdate, db: Session = Depends(get_db)):
    guide = crud.update_user(db, guide_id, data.model_dump(exclude_none=True))
    if not guide:
        raise HTTPException(status_code=404, detail="Bergführer nicht gefunden")
    return guide


# ---------------------------------------------------------------------------
# Tours
# ---------------------------------------------------------------------------

@app.post("/guides/{guide_id}/touren/", response_model=schemas.TourResponse, status_code=201, summary="Tour anlegen")
def create_tour(guide_id: int, tour: schemas.TourCreate, db: Session = Depends(get_db)):
    if not crud.get_user(db, guide_id):
        raise HTTPException(status_code=404, detail="Bergführer nicht gefunden")
    return crud.create_tour(db, tour, guide_id)


@app.get("/guides/{guide_id}/touren/", response_model=list[schemas.TourResponse], summary="Touren eines Bergführers")
def list_tours(guide_id: int, db: Session = Depends(get_db)):
    if not crud.get_user(db, guide_id):
        raise HTTPException(status_code=404, detail="Bergführer nicht gefunden")
    return crud.get_tours_for_guide(db, guide_id)


# ---------------------------------------------------------------------------
# Timeslots
# ---------------------------------------------------------------------------

@app.post("/guides/{guide_id}/timeslots/", response_model=schemas.TimeslotResponse, status_code=201, summary="Timeslot anlegen")
def create_timeslot(guide_id: int, slot: schemas.TimeslotCreate, db: Session = Depends(get_db)):
    if not crud.get_user(db, guide_id):
        raise HTTPException(status_code=404, detail="Bergführer nicht gefunden")
    return crud.create_timeslot(db, slot, guide_id)


@app.get("/guides/{guide_id}/timeslots/", response_model=list[schemas.TimeslotResponse], summary="Timeslots eines Bergführers")
def list_timeslots(guide_id: int, db: Session = Depends(get_db)):
    if not crud.get_user(db, guide_id):
        raise HTTPException(status_code=404, detail="Bergführer nicht gefunden")
    return crud.get_timeslots_for_guide(db, guide_id)


@app.patch("/timeslots/{timeslot_id}/status", response_model=schemas.TimeslotResponse, summary="Timeslot-Status aktualisieren")
def update_timeslot_status(timeslot_id: int, status: schemas.TimeslotStatusUpdate, db: Session = Depends(get_db)):
    slot = crud.update_timeslot_status(db, timeslot_id, status.status)
    if not slot:
        raise HTTPException(status_code=404, detail="Timeslot nicht gefunden")
    return slot


# ---------------------------------------------------------------------------
# Bookings
# ---------------------------------------------------------------------------

@app.post("/bookings/", response_model=schemas.BookingResponse, status_code=201, summary="Buchungsanfrage erstellen")
def create_booking(booking: schemas.BookingCreate, db: Session = Depends(get_db)):
    result = crud.create_booking(db, booking)
    if not result:
        raise HTTPException(status_code=409, detail="Timeslot nicht verfügbar")

    # Send notification emails (non-blocking — errors are logged, not raised)
    timeslot = db.query(models.Timeslot).filter(models.Timeslot.id == result.timeslot_id).first()
    guide = crud.get_user(db, timeslot.user_id) if timeslot else None
    date_str = timeslot.date.isoformat() if timeslot else "–"

    if guide:
        notify_guide_new_booking(
            guide_email=guide.email,
            guide_name=guide.name,
            guest_name=result.guest_name,
            guest_email=result.guest_email,
            date=date_str,
        )
    notify_guest_booking_received(
        guest_email=result.guest_email,
        guest_name=result.guest_name,
        guide_name=guide.name if guide else "Bergführer",
        date=date_str,
    )
    return result


@app.post("/bookings/{booking_id}/mark-paid", summary="Zahlung manuell als erhalten markieren")
def mark_booking_paid(booking_id: int, db: Session = Depends(get_db)):
    booking = db.query(models.Booking).filter(models.Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Buchung nicht gefunden")
    booking.payment_status = "paid"
    db.commit()
    return {"status": "paid"}


@app.post("/bookings/{booking_id}/modify", response_model=schemas.BookingDetailResponse, summary="Buchungsanfrage vom Bergführer modifizieren")
def modify_booking(booking_id: int, modification: schemas.BookingModify, db: Session = Depends(get_db)):
    booking = crud.modify_booking(db, booking_id, modification)
    if not booking:
        raise HTTPException(status_code=404, detail="Buchung nicht gefunden")
    
    # Send modification email to customer
    guide = crud.get_user(db, booking.timeslot.user_id) if booking.timeslot else None
    if guide:
        from .email import _send
        mod_info = []
        if modification.modified_date:
            mod_info.append(f"Neues Datum: <b>{modification.modified_date.isoformat()}</b>")
        if modification.modified_price:
            mod_info.append(f"Neuer Preis: <b>CHF {modification.modified_price}</b>")
        if modification.modified_tour_name:
            mod_info.append(f"Aktivität: <b>{modification.modified_tour_name}</b>")
        
        changes_html = "<br>".join(mod_info) if mod_info else "Keine Änderungen"
        
        _send(
            booking.guest_email,
            f"Modifizierte Buchungsanfrage von {guide.name}",
            f"""
            <p>Hallo {booking.guest_name},</p>
            <p>{guide.name} hat deine Buchungsanfrage modifiziert:</p>
            <p>{changes_html}</p>
            <p>Bitte überprüfe die neuen Details und bestätige, dass sie für dich ok sind.</p>
            <p>Sobald du bestätigst, kannst du die Zahlung abschliessen.</p>
            """
        )
    
    return _booking_to_detail_response(booking, db)


@app.post("/bookings/{booking_id}/accept-modification", response_model=schemas.BookingDetailResponse, summary="Modifizierte Buchung akzeptieren")
def accept_booking_modification(booking_id: int, db: Session = Depends(get_db)):
    booking = crud.accept_booking_modification(db, booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Buchung nicht gefunden")
    
    # Send confirmation email
    guide = crud.get_user(db, booking.timeslot.user_id) if booking.timeslot else None
    if guide:
        from .email import _send
        _send(
            guide.email,
            f"Buchung akzeptiert – {booking.guest_name}",
            f"""
            <p>Hallo {guide.name},</p>
            <p><b>{booking.guest_name}</b> hat deine Modifikation akzeptiert.</p>
            <p>Die Buchung ist jetzt bereit für die Zahlung.</p>
            """
        )
    
    return _booking_to_detail_response(booking, db)


@app.post("/bookings/{booking_id}/reject", summary="Buchung ablehnen")
def reject_booking_endpoint(booking_id: int, db: Session = Depends(get_db)):
    booking = crud.reject_booking(db, booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Buchung nicht gefunden")
    return {"status": "rejected"}


def _booking_to_detail_response(booking: models.Booking, db: Session) -> schemas.BookingDetailResponse:
    """Helper function to convert Booking to BookingDetailResponse"""
    return schemas.BookingDetailResponse(
        id=booking.id,
        guest_name=booking.guest_name,
        guest_email=booking.guest_email,
        guest_phone=booking.guest_phone,
        guest_notes=booking.guest_notes,
        payment_status=booking.payment_status,
        booking_status=booking.booking_status,
        timeslot_id=booking.timeslot_id,
        slot_date=booking.timeslot.date if booking.timeslot else None,
        guide_id=booking.timeslot.user_id if booking.timeslot else None,
        slot_status=booking.timeslot.status if booking.timeslot else None,
        guide_modified_date=booking.guide_modified_date,
        guide_modified_price=booking.guide_modified_price,
        guide_modified_tour_name=booking.guide_modified_tour_name,
        modification_sent_at=booking.modification_sent_at,
    )


@app.get("/bookings/", response_model=list[schemas.BookingDetailResponse], summary="Alle Buchungsanfragen")
def list_bookings(db: Session = Depends(get_db)):
    bookings = crud.get_all_bookings(db)
    result = []
    for b in bookings:
        result.append(_booking_to_detail_response(b, db))
    return result


# ---------------------------------------------------------------------------
# Timeslot delete
# ---------------------------------------------------------------------------

@app.delete("/timeslots/{timeslot_id}", status_code=204, summary="Timeslot löschen")
def delete_timeslot(timeslot_id: int, db: Session = Depends(get_db)):
    if not crud.delete_timeslot(db, timeslot_id):
        raise HTTPException(status_code=404, detail="Timeslot nicht gefunden")


# ---------------------------------------------------------------------------
# Google Calendar iCal sync
# ---------------------------------------------------------------------------

@app.post("/guides/{guide_id}/sync-calendar", summary="Google Calendar per iCal-URL synchronisieren")
def sync_google_calendar(guide_id: int, body: schemas.CalendarSyncRequest, db: Session = Depends(get_db)):
    guide = crud.get_user(db, guide_id)
    if not guide:
        raise HTTPException(status_code=404, detail="Bergführer nicht gefunden")

    ical_url = body.ical_url or guide.ical_url
    if not ical_url:
        raise HTTPException(status_code=400, detail="Keine iCal-URL hinterlegt")

    try:
        resp = http_requests.get(ical_url, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Kalender konnte nicht geladen werden: {e}")

    cal = Calendar.from_ical(resp.content)
    today = datetime.date.today()
    created = 0

    for component in cal.walk():
        if component.name != "VEVENT":
            continue
        dtstart = component.get("DTSTART")
        dtend = component.get("DTEND")
        if not dtstart:
            continue
        start = dtstart.dt
        end = dtend.dt if dtend else start
        if hasattr(start, "date"):
            start = start.date()
        if hasattr(end, "date"):
            end = end.date()
        current = start
        while current < end:
            if current >= today:
                crud.upsert_blocked_timeslot(db, guide_id, current)
                created += 1
            current += datetime.timedelta(days=1)

    return {"blocked_dates_added": created}


# ---------------------------------------------------------------------------
# Guide Dashboard
# ---------------------------------------------------------------------------

@app.get("/dashboard/{guide_id}", response_class=HTMLResponse, summary="Bergführer-Dashboard")
def get_dashboard(request: Request, guide_id: int, db: Session = Depends(get_db)):
    guide = crud.get_user(db, guide_id)
    if not guide:
        raise HTTPException(status_code=404, detail="Bergführer nicht gefunden")
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "guide_id": guide_id,
        "guide_name": guide.name,
    })


# ---------------------------------------------------------------------------
# Stripe Payment
# ---------------------------------------------------------------------------

@app.post("/bookings/{booking_id}/pay", summary="Stripe Checkout Session erstellen")
def create_checkout_session(booking_id: int, db: Session = Depends(get_db)):
    if not stripe.api_key or stripe.api_key.startswith("sk_test_your"):
        raise HTTPException(status_code=501, detail="Stripe nicht konfiguriert")

    booking = db.query(models.Booking).filter(models.Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Buchung nicht gefunden")

    timeslot = booking.timeslot
    tour = timeslot.tour if timeslot else None
    price_chf = int((tour.price if tour else 100) * 100)  # Stripe uses cents

    session = stripe.checkout.Session.create(
        payment_method_types=["card", "twint"],
        line_items=[{
            "price_data": {
                "currency": "chf",
                "product_data": {
                    "name": tour.title if tour else "Bergführer-Buchung",
                    "description": f"Datum: {timeslot.date.isoformat() if timeslot else '–'}",
                },
                "unit_amount": price_chf,
            },
            "quantity": 1,
        }],
        mode="payment",
        success_url=f"{APP_BASE_URL}/payment/success?booking_id={booking_id}",
        cancel_url=f"{APP_BASE_URL}/payment/cancel?booking_id={booking_id}",
        metadata={"booking_id": str(booking_id)},
    )
    return {"checkout_url": session.url}


@app.post("/stripe/webhook", summary="Stripe Webhook (intern)")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        booking_id = int(session["metadata"].get("booking_id", 0))
        booking = db.query(models.Booking).filter(models.Booking.id == booking_id).first()
        if booking:
            booking.payment_status = "paid"
            if booking.timeslot:
                booking.timeslot.status = "gebucht"
            db.commit()
            # Notify guide
            guide = crud.get_user(db, booking.timeslot.user_id) if booking.timeslot else None
            if guide:
                from .email import _send
                _send(
                    guide.email,
                    f"Zahlung erhalten – {booking.timeslot.date}",
                    f"<p>Hallo {guide.name},</p><p>Die Zahlung von <b>{booking.guest_name}</b> "
                    f"für den <b>{booking.timeslot.date}</b> ist eingegangen. "
                    f"Die Buchung ist jetzt bestätigt.</p>"
                )
    return {"status": "ok"}


@app.get("/payment/success", response_class=HTMLResponse, summary="Zahlungsbestätigung")
def payment_success(booking_id: int, db: Session = Depends(get_db)):
    booking = db.query(models.Booking).filter(models.Booking.id == booking_id).first()
    name = booking.guest_name if booking else "Gast"
    return HTMLResponse(f"""
    <html><body style="font-family:sans-serif;text-align:center;padding:60px">
      <h1 style="color:#059669">✓ Zahlung erfolgreich!</h1>
      <p>Danke {name}, deine Buchung ist bestätigt.</p>
      <p style="color:#888">Du erhältst eine Bestätigungs-E-Mail.</p>
    </body></html>
    """)


@app.get("/payment/cancel", response_class=HTMLResponse, summary="Zahlung abgebrochen")
def payment_cancel(booking_id: int):
    return HTMLResponse("""
    <html><body style="font-family:sans-serif;text-align:center;padding:60px">
      <h1 style="color:#dc2626">Zahlung abgebrochen</h1>
      <p>Deine Buchungsanfrage bleibt bestehen. Du kannst die Zahlung später abschliessen.</p>
    </body></html>
    """)

