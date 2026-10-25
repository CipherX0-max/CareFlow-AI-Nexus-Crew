"""Shared intake validation: age range and mobile-number normalisation (E.164-style)."""
import re

from .config import get_settings


def validate_age(age: int) -> int:
    s = get_settings()
    if not (s.min_patient_age <= age <= s.max_patient_age):
        raise ValueError(f"Age must be between {s.min_patient_age} and {s.max_patient_age}.")
    return age


def normalise_mobile(raw: str) -> str:
    """Return a consistent '+<digits>' form. A bare 10-digit number is assumed to use the configured default country code."""
    s = get_settings()
    v = re.sub(r"[\s\-().]", "", raw or "")
    if v.startswith("00"):
        v = "+" + v[2:]
    if not v.startswith("+"):
        v = v.lstrip("0") if len(v.lstrip("0")) == 10 else v
        if re.fullmatch(r"\d{10}", v):
            v = f"+{s.default_country_code}{v}"
        else:
            raise ValueError("Enter a valid mobile number, including the country code if it is not a local number.")
    if not re.fullmatch(r"\+[1-9]\d{7,14}", v):
        raise ValueError("Enter a valid mobile number (8-15 digits).")
    return v


def mask_mobile(m: str | None) -> str | None:
    return None if not m else m[:3] + "*" * max(0, len(m) - 5) + m[-2:]
