"""Deterministic text normalization shared by extractors and the grounding check.

Nothing here guesses: when input can't be parsed unambiguously, functions return None.
"""

import re
from decimal import Decimal, InvalidOperation

# Arabic-Indic (U+0660..0669) and Eastern Arabic / Persian (U+06F0..06F9) digits.
_DIGIT_MAP = {ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
_DIGIT_MAP.update({ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")})
_DIGIT_MAP[ord("٫")] = "."  # Arabic decimal separator
_DIGIT_MAP[ord("٬")] = ","  # Arabic thousands separator


def to_ascii_digits(text: str) -> str:
    """Convert Arabic-Indic / Persian digits and separators to ASCII."""
    return text.translate(_DIGIT_MAP)


# --- Amounts -----------------------------------------------------------------

_MULTIPLIERS = {
    "k": 1_000,
    "ألف": 1_000, "الف": 1_000, "آلاف": 1_000, "ألاف": 1_000, "الاف": 1_000,
    "m": 1_000_000, "mn": 1_000_000, "million": 1_000_000,
    "مليون": 1_000_000, "ملايين": 1_000_000,
}

_SUFFIX = r"k|mn|m|million|ألف|الف|آلاف|ألاف|الاف|مليون|ملايين"
_SPLIT_RE = re.compile(rf"^\s*(?P<num>[\d.,]+)\s*(?P<suffix>{_SUFFIX})?\s*$", re.IGNORECASE)

# With a suffix the dot is always a decimal point: 2.5M, 2.500M, 1,250.5K.
_NUM_WITH_SUFFIX = re.compile(r"^(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
# Without a suffix (DECISIONS.md D4):
_COMMA_GROUPED = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d+)?$")  # 3,200,000 / 3,200,000.50
_DOT_GROUPED = re.compile(r"^\d{1,3}(?:\.\d{3})+$")             # 2.500 / 1.200.000 → thousands
_PLAIN = re.compile(r"^\d+$")


def parse_amount(raw: str | None) -> int | float | None:
    """Parse a single price amount string to a number. Never guesses; returns None instead.

    Rules (DECISIONS.md D4):
    - With a suffix (K/M/million/ألف/مليون): the dot is a decimal point. '2.500M' → 2,500,000.
    - Without a suffix: commas are thousands separators; a dot followed by exact groups of
      3 digits is also a thousands separator ('2.500' → 2,500). Any other dot without a
      suffix ('2.5', '2.75') is ambiguous — probably a missing 'M' — so it returns None.
    Currency words and periods ('SAR', '/yr') must be stripped by the caller.
    """
    if raw is None:
        return None
    m = _SPLIT_RE.match(to_ascii_digits(raw))
    if not m:
        return None
    num, suffix = m.group("num"), m.group("suffix")

    if suffix:
        if not _NUM_WITH_SUFFIX.match(num):
            return None
        text = num.replace(",", "")
    elif _PLAIN.match(num):
        text = num
    elif _COMMA_GROUPED.match(num):
        text = num.replace(",", "")
    elif _DOT_GROUPED.match(num):
        text = num.replace(".", "")
    else:
        return None

    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if suffix:
        value *= _MULTIPLIERS[suffix.lower()]
    return int(value) if value == value.to_integral_value() else float(value)


# --- Phones ------------------------------------------------------------------

# Shaped like a Saudi mobile: optional +966/00966/966 or 0, then 5X, then exactly 7 digits
# (spaces/dashes allowed between digits). Digit boundaries stop it from swallowing a
# neighbouring number, e.g. "0551234567 5 BR".
_PHONE_CANDIDATE_RE = re.compile(
    r"(?<!\d)(?:(?:\+|00)?966[\s\-]?)?\(?0?5\d\)?(?:[\s\-]?\d){7}(?!\d)"
)


def normalize_saudi_mobile(raw: str | None) -> str | None:
    """Normalize a Saudi mobile number to E.164 (+9665XXXXXXXX), or None if it isn't one.

    Accepts 05XXXXXXXX, 5XXXXXXXX, 9665XXXXXXXX, +9665XXXXXXXX, 009665XXXXXXXX,
    with spaces/dashes/parentheses and Arabic digits. Landlines and foreign numbers → None.
    """
    if not raw:
        return None
    digits = re.sub(r"\D", "", to_ascii_digits(raw))
    if digits.startswith("00966"):
        digits = digits[5:]
    elif digits.startswith("966"):
        digits = digits[3:]
    elif digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 9 and digits.startswith("5"):
        return "+966" + digits
    return None


def find_saudi_mobiles(text: str) -> list[str]:
    """All distinct Saudi mobiles found in free text, normalized, in order of appearance."""
    found: list[str] = []
    for m in _PHONE_CANDIDATE_RE.finditer(to_ascii_digits(text)):
        phone = normalize_saudi_mobile(m.group())
        if phone and phone not in found:
            found.append(phone)
    return found
