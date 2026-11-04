"""Two-factor login with a TOTP app (12-datenschutz.md, level B). Recovery codes are stored hashed."""

import hashlib
import secrets

import pyotp
import segno
from django.core.cache import cache

ISSUER = "Tourchef"
RECOVERY_CODE_COUNT = 8


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(user, secret: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=ISSUER)


def qr_svg(uri: str) -> str:
    """Inline SVG (no external request, fits the strict CSP)."""
    return segno.make(uri, error="m").svg_inline(scale=5, dark="#1f2a30", light="#ffffff")


def _hash(code: str) -> str:
    return hashlib.sha256(code.strip().lower().replace("-", "").encode()).hexdigest()


def _totp_ok(secret: str, code: str, user_pk) -> bool:
    code = code.strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6 and secret):
        return False
    if not pyotp.TOTP(secret).verify(code, valid_window=1):
        return False
    # Each code works once (replay protection within its 90-second window).
    return cache.add(f"totp-used:{user_pk}:{code}", 1, 120)


def verify(user, code: str) -> bool:
    """A current TOTP code, or an unused recovery code (which is then used up)."""
    if _totp_ok(user.totp_secret, code, user.pk):
        return True
    hashed = _hash(code)
    codes = user.recovery_codes.split()
    if hashed in codes:
        codes.remove(hashed)
        user.recovery_codes = " ".join(codes)
        user.save(update_fields=["recovery_codes"])
        return True
    return False


def check_setup_code(secret: str, code: str, user_pk) -> bool:
    return _totp_ok(secret, code, user_pk)


def new_recovery_codes(user) -> list[str]:
    codes = [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(RECOVERY_CODE_COUNT)]
    user.recovery_codes = " ".join(_hash(c) for c in codes)
    return codes
