"""Duplicate detection (PLAN.md §8 as amended in DECISIONS.md D8): exact matches only.

A match never changes listing data. It only sets `duplicate_of` (and, for the weaker
match, the review reason `possible_duplicate`).
"""

from app.models import Listing

# Attributes that, when stated on both sides with different values, mean "different property".
_CONFLICT_FIELDS = ("purpose", "bedrooms", "bathrooms", "area_sqm", "land_area_sqm")


def _both(a, b) -> bool:
    return a is not None and b is not None


def _conflict(new: Listing, old: Listing) -> bool:
    return any(
        _both(getattr(new, f), getattr(old, f)) and getattr(new, f) != getattr(old, f)
        for f in _CONFLICT_FIELDS
    )


def _same(a, b) -> bool:
    return _both(a, b) and a == b


def is_strong_match(new: Listing, old: Listing) -> bool:
    """Same broker phone, type and district, and the exact same price or area."""
    return (
        _same(new.contact_phone, old.contact_phone)
        and _same(new.property_type, old.property_type)
        and _same(new.district, old.district)
        and (_same(new.price.amount, old.price.amount) or _same(new.area_sqm, old.area_sqm))
        and not _conflict(new, old)
    )


def is_weak_match(new: Listing, old: Listing) -> bool:
    """A different broker reposting: same purpose, type, district, bedrooms and exact price."""
    return (
        _same(new.purpose, old.purpose)
        and _same(new.property_type, old.property_type)
        and _same(new.district, old.district)
        and _same(new.bedrooms, old.bedrooms)
        and _same(new.price.amount, old.price.amount)
        and not _conflict(new, old)
    )


def find_duplicate(new: Listing, existing: list[Listing]) -> tuple[str, bool] | None:
    """Return (canonical id, is_weak) for the earliest matching listing, or None.

    `existing` must be in insertion order and exclude listings from the same message.
    The canonical id is the root of the chain: a duplicate's own `duplicate_of`.
    """
    for weak, matcher in ((False, is_strong_match), (True, is_weak_match)):
        for old in existing:
            if matcher(new, old):
                return (old.duplicate_of or old.id), weak
    return None


def apply_dedup(new: Listing, existing: list[Listing]) -> Listing:
    found = find_duplicate(new, existing)
    if found is None:
        return new
    canonical, weak = found
    reasons = new.review_reasons + (["possible_duplicate"] if weak else [])
    return new.model_copy(update={"duplicate_of": canonical, "review_reasons": reasons})
