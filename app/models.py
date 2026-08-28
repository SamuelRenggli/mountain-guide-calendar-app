from sqlalchemy import Column, Integer, String, Float, Date, DateTime, ForeignKey, Enum, Text
from sqlalchemy.orm import relationship
from .database import Base
from datetime import datetime


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=False)
    stripe_account_id = Column(String(100), nullable=True)
    ical_url = Column(String(500), nullable=True)

    touren = relationship("Tour", back_populates="guide")
    timeslots = relationship("Timeslot", back_populates="guide")


class Tour(Base):
    __tablename__ = "touren"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(String(1000), nullable=True)
    price = Column(Float, nullable=False)
    max_participants = Column(Integer, nullable=False, default=10)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    guide = relationship("User", back_populates="touren")
    timeslots = relationship("Timeslot", back_populates="tour")


class Timeslot(Base):
    __tablename__ = "timeslots"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False, index=True)
    status = Column(
        Enum("frei", "gebucht", "anfrage", "blockiert"),
        nullable=False,
        default="frei",
    )
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    tour_id = Column(Integer, ForeignKey("touren.id"), nullable=True)

    guide = relationship("User", back_populates="timeslots")
    tour = relationship("Tour", back_populates="timeslots")
    booking = relationship("Booking", back_populates="timeslot", uselist=False)


class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, index=True)
    guest_name = Column(String(100), nullable=False)
    guest_email = Column(String(100), nullable=False, index=True)
    guest_phone = Column(String(20), nullable=True)
    guest_notes = Column(Text, nullable=True)
    payment_status = Column(
        Enum("pending", "paid"),
        nullable=False,
        default="pending",
    )
    booking_status = Column(
        Enum("pending", "awaiting_modification", "modified", "accepted", "rejected"),
        nullable=False,
        default="pending",
    )
    timeslot_id = Column(Integer, ForeignKey("timeslots.id"), nullable=False)
    
    # Guide modification fields
    guide_modified_date = Column(Date, nullable=True)
    guide_modified_price = Column(Float, nullable=True)
    guide_modified_tour_name = Column(String(200), nullable=True)
    modification_sent_at = Column(DateTime, nullable=True)

    timeslot = relationship("Timeslot", back_populates="booking")
