from datetime import date, datetime
from typing import Literal, Optional, Union
from pydantic import BaseModel, EmailStr


# --- User ---

class UserCreate(BaseModel):
    name: str
    email: EmailStr
    ical_url: Optional[str] = None


class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    ical_url: Optional[str] = None


class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    stripe_account_id: Optional[str] = None
    ical_url: Optional[str] = None

    model_config = {"from_attributes": True}


# --- Tour ---

class TourCreate(BaseModel):
    title: str
    description: Optional[str] = None
    price: float
    max_participants: int = 10


class TourResponse(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    price: float
    max_participants: int
    user_id: int

    model_config = {"from_attributes": True}


# --- Timeslot ---

class TimeslotCreate(BaseModel):
    date: date
    tour_id: Optional[int] = None
    status: Literal["frei", "gebucht", "anfrage", "blockiert"] = "frei"


class TimeslotStatusUpdate(BaseModel):
    status: Literal["frei", "gebucht", "anfrage", "blockiert"]


class TimeslotResponse(BaseModel):
    id: int
    date: date
    status: str
    user_id: int
    tour_id: Optional[int] = None

    model_config = {"from_attributes": True}


# --- Booking ---

class BookingCreate(BaseModel):
    guest_name: str
    guest_email: EmailStr
    guest_phone: Optional[str] = None
    guest_notes: Optional[str] = None
    timeslot_id: int


class BookingResponse(BaseModel):
    id: int
    guest_name: str
    guest_email: str
    guest_phone: Optional[str] = None
    guest_notes: Optional[str] = None
    payment_status: str
    booking_status: str
    timeslot_id: int

    model_config = {"from_attributes": True}


class BookingDetailResponse(BaseModel):
    id: int
    guest_name: str
    guest_email: str
    guest_phone: Optional[str] = None
    guest_notes: Optional[str] = None
    payment_status: str
    booking_status: str
    timeslot_id: int
    slot_date: Union[date, None] = None
    guide_id: Union[int, None] = None
    slot_status: Union[str, None] = None
    guide_modified_date: Union[date, None] = None
    guide_modified_price: Union[float, None] = None
    guide_modified_tour_name: Union[str, None] = None
    modification_sent_at: Union[datetime, None] = None

    model_config = {"from_attributes": True}


class BookingModify(BaseModel):
    modified_date: Optional[date] = None
    modified_price: Optional[float] = None
    modified_tour_name: Optional[str] = None
    message: Optional[str] = None


class CalendarSyncRequest(BaseModel):
    ical_url: Optional[str] = None
