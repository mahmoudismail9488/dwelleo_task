"""Grounding check: every value an extractor claims must be traceable to the message.

This is the main defence against invented LLM output (PLAN.md §6). It runs on every
candidate, whichever extractor produced it. Ungrounded values become None and the
listing gets `<field>_unverified`. Nothing is ever *added* here.
"""

import re
from decimal import Decimal, InvalidOperation

from app.gazetteer import CITIES, DISTRICTS, find_places, fold
from app.models import (
    ExtractionCandidate,
    ListingCandidate,
    PriceMention,
    PricePeriod,
    ProjectCandidate,
    PropertyType,
    Purpose,
)
from app.textnorm import find_saudi_mobiles, normalize_saudi_mobile, parse_amount

_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_AMOUNT_NOISE_RE = re.compile(
    r"(?i)\b(?:sar|riyals?)\b|ريال|ر\.س|/\s*\w+|\bper\s+\w+|سنوي[اً]*|شهري[اً]*|\s+"
)
# Bedroom number words that count as "stated" (غرفتين = 2, غرفة نوم = 1).
_BEDROOM_WORDS = {2: re.compile(r"غرفتين|غرفتان"), 1: re.compile(r"غرفه\s*نوم")}


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", fold(text)).strip()


def numbers_in(text: str) -> set[Decimal]:
    out = set()
    for m in _NUMBER_RE.finditer(fold(text)):
        try:
            out.add(Decimal(m.group().replace(",", "")))
        except InvalidOperation:
            pass
    return out


def clean_amount_text(raw: str) -> str:
    """Strip currency words, periods and spaces around an amount: '3.2M SAR' → '3.2M'."""
    return _AMOUNT_NOISE_RE.sub(" ", raw).strip()


def amount_is_grounded(raw: str | None, text: str) -> bool:
    """The amount text appears in the message and parses to a number."""
    if not raw:
        return False
    return _squash(raw) in _squash(text) and parse_amount(clean_amount_text(raw)) is not None


def resolve_price(mention: PriceMention | None, text: str) -> tuple[PriceMention | None, list[str]]:
    """Check a cited price (D6). Returns the mention with a trusted `amount`, or None.

    - quote not in the message                → dropped, `price_unverified`
    - our parser reads the quote              → our value wins; if the extractor's
                                                 amount differs, `price_llm_mismatch`
    - our parser can't read it, amount given  → extractor's amount kept, `price_interpreted`
    - our parser can't read it, no amount     → dropped, `price_unverified`
    """
    if mention is None:
        return None, []
    if _squash(mention.quote) not in _squash(text):
        return None, ["price_unverified"]
    parsed = parse_amount(clean_amount_text(mention.quote))
    if parsed is not None:
        reasons = ["price_llm_mismatch"] if mention.amount is not None and mention.amount != parsed else []
        return mention.model_copy(update={"amount": parsed}), reasons
    if mention.amount is not None and mention.amount > 0:
        return mention, ["price_interpreted"]
    return None, ["price_unverified"]


def number_is_grounded(value: float | int | None, text: str, words: dict | None = None) -> bool:
    if value is None:
        return False
    if Decimal(str(value)) in numbers_in(text):
        return True
    pattern = (words or {}).get(value)
    return bool(pattern and pattern.search(fold(text)))


def _strip_article(name: str) -> str:
    return re.sub(r"^(?:al|el)[\s\-]+|^ال", "", _squash(name))


def _word_in(name: str, text: str) -> bool:
    """Whole-word match, allowing an article/clitic prefix (Al-, ال, بال…) in the text."""
    pattern = rf"(?<!\w)(?:(?:al|el)[\s\-]?|[وبفك]?ال|لل)?{re.escape(name)}(?!\w)"
    return re.search(pattern, _squash(text)) is not None


def place_is_grounded(name: str | None, text: str, places) -> bool:
    """The name is written in the message, directly or via a gazetteer alias found in it."""
    if not name:
        return False
    stripped = _strip_article(name)
    if stripped and _word_in(stripped, text):
        return True
    named = {m.place for m in find_places(name, places)}
    return bool(named) and any(m.place in named for m in find_places(text, places))


def phone_is_grounded(raw: str | None, text: str) -> bool:
    """The phone must be written in the message text. The sender's number does NOT count:
    using it is an inference, done (and flagged) only by review.py (DECISIONS.md D9)."""
    phone = normalize_saudi_mobile(raw)
    return phone is not None and phone in find_saudi_mobiles(text)


def _valid_enum(value: str | None, enum) -> bool:
    return value is None or value in {e.value for e in enum}


def ground_listing(
    c: ListingCandidate, text: str, sender: str | None
) -> tuple[ListingCandidate, list[str]]:
    reasons: list[str] = []
    upd: dict = {}

    def reject(field: str, reason: str, value=None):
        upd[field] = value
        reasons.append(reason)

    for field, enum in (("purpose", Purpose), ("property_type", PropertyType), ("price_period", PricePeriod)):
        if not _valid_enum(getattr(c, field), enum):
            reject(field, f"{field}_unverified")

    if c.bedrooms is not None and not number_is_grounded(c.bedrooms, text, _BEDROOM_WORDS):
        reject("bedrooms", "bedrooms_unverified")
    if c.bathrooms is not None and not number_is_grounded(c.bathrooms, text):
        reject("bathrooms", "bathrooms_unverified")
    for field in ("built_up_area_sqm", "land_area_sqm"):
        if getattr(c, field) is not None and not number_is_grounded(getattr(c, field), text):
            reject(field, "area_unverified")

    kept = []
    for mention in c.prices:
        resolved, r = resolve_price(mention, text)
        reasons.extend(r)
        if resolved is not None:
            kept.append(resolved)
    upd["prices"] = kept
    per_sqm, r = resolve_price(c.price_per_sqm, text)
    reasons.extend(r)
    upd["price_per_sqm"] = per_sqm

    if c.city and not place_is_grounded(c.city, text, CITIES):
        reject("city", "city_unverified")
    if c.district and not place_is_grounded(c.district, text, DISTRICTS):
        reject("district", "district_unverified")
    if c.contact_phone_text and not phone_is_grounded(c.contact_phone_text, text):
        reject("contact_phone_text", "contact_phone_unverified")

    return c.model_copy(update=upd), list(dict.fromkeys(reasons))


def ground_project(c: ProjectCandidate, text: str) -> tuple[ProjectCandidate, list[str]]:
    upd: dict = {}
    reasons: list[str] = []

    def reject(field: str, reason: str):
        upd[field] = None
        reasons.append(reason)

    if c.name and _squash(c.name) not in _squash(text):
        reject("name", "project_name_unverified")
    if c.city and not place_is_grounded(c.city, text, CITIES):
        reject("city", "city_unverified")
    if c.district and not place_is_grounded(c.district, text, DISTRICTS):
        reject("district", "district_unverified")
    min_price, r = resolve_price(c.min_price, text)
    upd["min_price"] = min_price
    reasons.extend(r)
    for field in ("min_bedrooms", "max_bedrooms"):
        if getattr(c, field) is not None and not number_is_grounded(getattr(c, field), text):
            reject(field, "bedrooms_unverified")
    for field in ("delivery_date", "payment_terms"):
        if getattr(c, field) and _squash(getattr(c, field)) not in _squash(text):
            reject(field, f"{field}_unverified")
    if c.contact_phone_text and not phone_is_grounded(c.contact_phone_text, text):
        reject("contact_phone_text", "contact_phone_unverified")
    return c.model_copy(update=upd), list(dict.fromkeys(reasons))


def ground(
    candidate: ExtractionCandidate, text: str, sender: str | None
) -> tuple[ExtractionCandidate, list[list[str]], list[str]]:
    """Ground a whole candidate. Returns it, one reason list per listing, and project reasons."""
    listings, reasons = [], []
    for lc in candidate.listings:
        grounded, r = ground_listing(lc, text, sender)
        listings.append(grounded)
        reasons.append(r)
    project, project_reasons = (
        ground_project(candidate.project, text) if candidate.project else (None, [])
    )
    return (
        candidate.model_copy(update={"listings": listings, "project": project}),
        reasons,
        project_reasons,
    )
