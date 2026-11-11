"""Turn a grounded candidate into the final contract objects, applying PLAN.md §5.

This is where policies, inferences and review reasons live. Every inferred value is
listed in `inferred_fields` and gets a review reason, except the two approved
conventions that carry no flag: studio → bedrooms 0, and sale → period "total".
"""

from app.gazetteer import CITIES, find_places, lookup_district
from app.models import ListingCandidate, Listing, Price, PriceCandidate, Project, ProjectCandidate
from app.textnorm import normalize_saudi_mobile

# Fixed vocabulary (PLAN.md §7, plus purpose_inferred approved in step 4).
REVIEW_REASONS = {
    "price_missing", "price_contradiction", "price_computed", "price_period_missing",
    "purpose_missing", "purpose_inferred", "property_type_missing", "city_missing",
    "city_inferred", "district_missing", "bedrooms_missing", "area_missing",
    "contact_phone_missing", "contact_phone_invalid", "contact_phone_from_sender",
    "llm_fallback_used", "llm_listing_count_suspicious", "possible_duplicate",
    # cited prices (D6)
    "price_llm_mismatch", "price_interpreted",
    # projects
    "project_name_missing",
    # grounding: <field>_unverified
    "purpose_unverified", "property_type_unverified", "price_period_unverified",
    "bedrooms_unverified", "bathrooms_unverified", "area_unverified", "price_unverified",
    "city_unverified", "district_unverified", "contact_phone_unverified",
    "project_name_unverified", "delivery_date_unverified", "payment_terms_unverified",
}


def canonical_city(name: str | None) -> str | None:
    if not name:
        return None
    found = {m.place.name for m in find_places(name, CITIES)}
    return found.pop() if len(found) == 1 else name


def canonical_district(name: str | None) -> str | None:
    if not name:
        return None
    place = lookup_district(name)
    return place.name if place else name


def _num(value: float | int | None) -> int | float | None:
    """3200000.0 → 3200000 so output stays clean."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def build_listing(
    c: ListingCandidate,
    listing_id: str,
    source: str,
    sender: str | None,
    extra_reasons: list[str] | None = None,
) -> Listing:
    reasons: list[str] = list(extra_reasons or [])
    inferred: list[str] = []

    # --- property type & purpose ---
    property_type = c.property_type
    if property_type is None:
        reasons.append("property_type_missing")
    purpose = c.purpose
    if purpose is None and c.price_period in ("yearly", "monthly"):
        purpose = "rent"  # a sale price is never quoted per year/month
        inferred.append("purpose")
        reasons.append("purpose_inferred")
    elif purpose is None:
        reasons.append("purpose_missing")

    # --- location ---
    district = canonical_district(c.district)
    city = canonical_city(c.city)
    if city is None and district:
        place = lookup_district(district)
        if place and not place.is_ambiguous:
            city = place.cities[0]
            inferred.append("city")
            reasons.append("city_inferred")
    if city is None:
        reasons.append("city_missing")
    if district is None:
        reasons.append("district_missing")

    # --- rooms ---
    bedrooms = c.bedrooms
    if bedrooms is None and property_type == "studio":
        bedrooms = 0  # convention, no flag (PLAN.md §5)
        inferred.append("bedrooms")
    elif bedrooms is None and property_type != "land":
        reasons.append("bedrooms_missing")

    # --- area: area_sqm is built-up; for land it's the plot area ---
    if property_type == "land":
        area = c.land_area_sqm if c.land_area_sqm is not None else c.built_up_area_sqm
        land_area = area
    else:
        area, land_area = c.built_up_area_sqm, c.land_area_sqm
    if area is None:
        reasons.append("area_missing")

    # --- price (D2; amounts already resolved by grounding, D6) ---
    stated = []
    for mention in c.prices:
        v = _num(mention.amount)
        if v is not None and v not in stated:
            stated.append(v)
    per_sqm = _num(c.price_per_sqm.amount) if c.price_per_sqm else None
    computed = _num(per_sqm * area) if per_sqm is not None and area is not None else None
    if "price_interpreted" in reasons:
        inferred.append("price.amount")

    candidates = [PriceCandidate(amount=v, basis="stated") for v in stated]
    amount = None
    if len(stated) > 1:
        reasons.append("price_contradiction")
    elif len(stated) == 1 and computed is not None and stated[0] != computed:  # exact (D2)
        reasons.append("price_contradiction")
        candidates = [PriceCandidate(amount=stated[0], basis="stated_total"),
                      PriceCandidate(amount=computed, basis="computed_from_per_sqm")]
    elif len(stated) == 1:
        amount, candidates = stated[0], []
    elif computed is not None:
        amount, candidates = computed, []
        if "price.amount" not in inferred:
            inferred.append("price.amount")
        reasons.append("price_computed")
    else:
        candidates = []
        reasons.append("price_missing")

    period = c.price_period
    if period is None and purpose == "sale":
        period = "total"  # a sale price is a total by definition
    elif period is None and purpose == "rent" and (amount is not None or len(stated) > 1):
        reasons.append("price_period_missing")

    phone = _contact(c.contact_phone_text, sender, reasons, inferred)

    return Listing(
        id=listing_id,
        purpose=purpose,
        property_type=property_type,
        city=city,
        district=district,
        bedrooms=bedrooms,
        bathrooms=c.bathrooms,
        area_sqm=area,
        land_area_sqm=land_area,
        price=Price(amount=amount, period=period),
        price_per_sqm=per_sqm,
        price_candidates=candidates,
        contact_phone=phone,
        review_reasons=list(dict.fromkeys(reasons)),
        inferred_fields=inferred,
        source=source,
    )


def _contact(text_phone: str | None, sender: str | None, reasons: list, inferred: list) -> str | None:
    """Phone from the message text; otherwise the sender's number, flagged (PLAN.md §5).
    Shared by listings and projects so the policy is the same for both."""
    phone = normalize_saudi_mobile(text_phone)
    if text_phone and phone is None:
        reasons.append("contact_phone_invalid")
    if phone is None and not text_phone:
        sender_phone = normalize_saudi_mobile(sender)
        if sender_phone:
            phone = sender_phone
            inferred.append("contact_phone")
            reasons.append("contact_phone_from_sender")
        else:
            reasons.append("contact_phone_missing")
    return phone


def build_project(
    c: ProjectCandidate, extra_reasons: list[str] | None = None, sender: str | None = None
) -> Project:
    reasons = list(extra_reasons or [])
    inferred: list[str] = []
    city = canonical_city(c.city)
    min_price = _num(c.min_price.amount) if c.min_price else None
    if c.name is None:
        reasons.append("project_name_missing")
    if city is None:
        reasons.append("city_missing")
    if min_price is None:
        reasons.append("price_missing")
    phone = _contact(c.contact_phone_text, sender, reasons, inferred)
    return Project(
        name=c.name,
        city=city,
        district=canonical_district(c.district),
        min_price=min_price,
        min_bedrooms=c.min_bedrooms,
        max_bedrooms=c.max_bedrooms,
        delivery_date=c.delivery_date,
        payment_terms=c.payment_terms,
        contact_phone=phone,
        review_reasons=list(dict.fromkeys(reasons)),
        inferred_fields=inferred,
    )
