"""Model field that stores its value encrypted (Fernet: AES-128-CBC + HMAC-SHA256).

Used for sensitive guest data (phone, emergency contact, experience). The keys live in
FIELD_ENCRYPTION_KEYS (Secret Manager), never in the database, so a database dump or backup alone
doesn't reveal these values. Encrypted values can't be searched or sorted in SQL.
"""

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.db import models

PREFIX = "enc1:"


@lru_cache(maxsize=4)
def _fernet(keys: tuple[str, ...], secret_key: str) -> MultiFernet:
    if not keys:  # development and tests only (settings refuse to start without keys when DEBUG is off)
        keys = (base64.urlsafe_b64encode(hashlib.sha256(f"dev-field-key:{secret_key}".encode()).digest()).decode(),)
    return MultiFernet([Fernet(key.encode()) for key in keys])


def fernet() -> MultiFernet:
    return _fernet(tuple(settings.FIELD_ENCRYPTION_KEYS), settings.SECRET_KEY)


def encrypt(value: str) -> str:
    if not value:
        return ""
    return PREFIX + fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    if not value:
        return ""
    if not value.startswith(PREFIX):
        return value  # written before encryption was introduced; re-encrypted by migration
    try:
        return fernet().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken:
        return "[nicht entschlüsselbar]"


class EncryptedTextField(models.TextField):
    """A text field encrypted at rest. Empty stays empty (so "is there a value?" needs no key)."""

    def __init__(self, *args, max_length=None, **kwargs):
        self.plain_max_length = max_length  # validated on the plain value, not the longer ciphertext
        kwargs.setdefault("blank", True)
        kwargs.setdefault("default", "")
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        if self.plain_max_length:
            kwargs["max_length"] = self.plain_max_length
        return name, path, args, kwargs

    def from_db_value(self, value, expression, connection):
        return decrypt(value) if value is not None else value

    def to_python(self, value):
        return decrypt(value) if isinstance(value, str) else value

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return encrypt(value) if value else ""

    def validate(self, value, model_instance):
        super().validate(value, model_instance)
        if self.plain_max_length and value and len(value) > self.plain_max_length:
            from django.core.exceptions import ValidationError

            raise ValidationError(f"Maximal {self.plain_max_length} Zeichen.")
