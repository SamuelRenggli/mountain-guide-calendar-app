from sqlalchemy.orm import Session
from datetime import datetime
from . import models, schemas


# --- Users ---

def get_user(db: Session, user_id: int):
    return db.query(models.User).filter(models.User.id == user_id).first()


def get_user_by_email(db: Session, email: str):
    return db.query(models.User).filter(models.User.email == email).first()


def create_user(db: Session, user: schemas.UserCreate):
    db_user = models.User(name=user.name, email=user.email, ical_url=user.ical_url)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


def update_user(db: Session, user_id: int, data: dict):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        return None
    for key, value in data.items():
        setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return user


# --- Tours ---

def get_tours_for_guide(db: Session, user_id: int):
    return db.query(models.Tour).filter(models.Tour.user_id == user_id).all()


def create_tour(db: Session, tour: schemas.TourCreate, user_id: int):
    db_tour = models.Tour(**tour.model_dump(), user_id=user_id)
    db.add(db_tour)
    db.commit()
    db.refresh(db_tour)
    return db_tour


# --- Timeslots ---

def get_timeslots_for_guide(db: Session, user_id: int):
    return db.query(models.Timeslot).filter(models.Timeslot.user_id == user_id).all()


def create_timeslot(db: Session, timeslot: schemas.TimeslotCreate, user_id: int):
    db_slot = models.Timeslot(**timeslot.model_dump(), user_id=user_id)
    db.add(db_slot)
    db.commit()
    db.refresh(db_slot)
    return db_slot


def update_timeslot_status(db: Session, timeslot_id: int, status: str):
    slot = db.query(models.Timeslot).filter(models.Timeslot.id == timeslot_id).first()
    if not slot:
        return None
    slot.status = status
    db.commit()
    db.refresh(slot)
    return slot


# --- Bookings ---

def create_booking(db: Session, booking: schemas.BookingCreate):
    timeslot = db.query(models.Timeslot).filter(
        models.Timeslot.id == booking.timeslot_id,
        models.Timeslot.status == "frei",
    ).first()
    if not timeslot:
        return None
    db_booking = models.Booking(
        guest_name=booking.guest_name,
        guest_email=booking.guest_email,
        guest_phone=booking.guest_phone,
        guest_notes=booking.guest_notes,
        timeslot_id=booking.timeslot_id,
        booking_status="pending",
    )
    timeslot.status = "anfrage"
    db.add(db_booking)
    db.commit()
    db.refresh(db_booking)
    return db_booking


def get_booking(db: Session, booking_id: int):
    return db.query(models.Booking).filter(models.Booking.id == booking_id).first()


def get_all_bookings(db: Session):
    return (
        db.query(models.Booking)
        .join(models.Timeslot)
        .order_by(models.Timeslot.date)
        .all()
    )


def modify_booking(db: Session, booking_id: int, modification: schemas.BookingModify):
    booking = db.query(models.Booking).filter(models.Booking.id == booking_id).first()
    if not booking:
        return None
    
    booking.guide_modified_date = modification.modified_date
    booking.guide_modified_price = modification.modified_price
    booking.guide_modified_tour_name = modification.modified_tour_name
    booking.booking_status = "awaiting_modification"
    booking.modification_sent_at = datetime.utcnow()
    
    db.commit()
    db.refresh(booking)
    return booking


def accept_booking_modification(db: Session, booking_id: int):
    booking = db.query(models.Booking).filter(models.Booking.id == booking_id).first()
    if not booking:
        return None
    
    booking.booking_status = "accepted"
    
    # If guide modified the date, update the timeslot
    if booking.guide_modified_date:
        # Create new timeslot with the modified date
        old_timeslot = booking.timeslot
        new_timeslot = db.query(models.Timeslot).filter(
            models.Timeslot.user_id == old_timeslot.user_id,
            models.Timeslot.date == booking.guide_modified_date,
            models.Timeslot.status == "frei",
        ).first()
        
        if new_timeslot:
            booking.timeslot_id = new_timeslot.id
            new_timeslot.status = "anfrage"
            old_timeslot.status = "frei"
    
    db.commit()
    db.refresh(booking)
    return booking


def reject_booking(db: Session, booking_id: int):
    booking = db.query(models.Booking).filter(models.Booking.id == booking_id).first()
    if not booking:
        return None
    
    booking.booking_status = "rejected"
    if booking.timeslot:
        booking.timeslot.status = "frei"
    
    db.commit()
    db.refresh(booking)
    return booking


def delete_timeslot(db: Session, timeslot_id: int):
    slot = db.query(models.Timeslot).filter(models.Timeslot.id == timeslot_id).first()
    if not slot:
        return False
    db.delete(slot)
    db.commit()
    return True


def upsert_blocked_timeslot(db: Session, user_id: int, date):
    """Create a blockiert timeslot for a date if none exists yet."""
    existing = db.query(models.Timeslot).filter(
        models.Timeslot.user_id == user_id,
        models.Timeslot.date == date,
        models.Timeslot.status == "blockiert",
    ).first()
    if existing:
        return existing
    slot = models.Timeslot(date=date, status="blockiert", user_id=user_id)
    db.add(slot)
    db.commit()
    db.refresh(slot)
    return slot
