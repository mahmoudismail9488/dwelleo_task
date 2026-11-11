"""Rule-based extractor: regex + keyword lists + the gazetteer. No network, deterministic.

It reports only what the text states. When something can't be read unambiguously, the
field stays None; the pipeline then adds the review reason.
"""

import re

from app.gazetteer import AR_LETTERS, CITIES, DISTRICTS, find_places, fold, lookup_district
from app.models import ExtractionCandidate, ListingCandidate, PriceMention, ProjectCandidate, RawMessage
from app.textnorm import find_saudi_mobiles, parse_amount

# --- Classification (precedence: buyer > project > listing > irrelevant) -------

_BUYER = [
    r"looking for", r"\bwanted\b", r"want to buy", r"searching for", r"\bbudget\b",
    r"\bcash buyer\b", r"\bneed (?:a|an)\b", r"مطلوب", r"ابحث عن", r"نبحث عن", r"ميزانيه",
]
_PROJECT = [
    r"off[\s\-]?plan", r"new launch", r"\blaunch(?:ing)?\b", r"\bhandover\b", r"payment plan",
    r"down\s*payment", r"على الخارطه", r"مشروع", r"تسليم", r"دفعه اولى", r"مقدم \d+\s*%",
]
_LISTING = [
    r"for sale", r"for rent", r"\bsale\b", r"\brent\b", r"للبيع", r"للايجار", r"ايجار",
    r"available", r"متاح",
]

# --- Property types (priority order: "land" last, it's also an area label) -----

_TYPES = [
    ("villa", r"\bvillas?\b|فيلا|فله"),
    ("townhouse", r"town\s*house|تاون\s*هاوس"),
    ("studio", r"\bstudios?\b|استوديو|ستوديو"),
    ("apartment", r"\bapartments?\b|\bflats?\b|شقه|شقق"),
    ("other", r"\bduplex\b|\boffice\b|\bshop\b|\bbuilding\b|\bwarehouse\b|عماره|محل|مكتب|مستودع|دوبلكس"),
    ("land", r"\bland\b|\bplot\b|ارض"),
]

_SALE = r"for sale|\bsale\b|للبيع|\bبيع\b"
_RENT = r"for rent|\brent\b|\blease\b|للايجار|ايجار"

# --- Numbers ------------------------------------------------------------------

_NUM = r"\d[\d,.]*"
# Boundaries use Arabic letters only, so "ألف،" (Arabic comma) still ends the suffix.
_SUFFIX = rf"(?:k|mn|m|million|الف|الاف|مليون|ملايين)(?![a-z{AR_LETTERS}])"
_AREA_UNIT = rf"(?:sqm|sq\.?\s?m|m2|m²|م²|م2|متر مربع|متر|م)(?![a-z{AR_LETTERS}0-9])"
_LAND_LABEL = r"\bland\b|\bplot\b|ارض"
_BUA_LABEL = r"\bbua\b|built[\s\-]?up|\bbuilding\b|بناء|مسطح"

_BEDROOM_RANGE_RE = re.compile(rf"(\d+)\s*[-–—]\s*(\d+)\s*(?:br\b|bed|غرف)")
_BEDROOM_RE = re.compile(r"(\d+)\s*(?:br\b|bhk\b|beds?\b|bedrooms?\b|غرف(?:ه)?(?:\s*نوم)?)")
_BATH_RE = re.compile(r"(\d+)\s*(?:baths?\b|bathrooms?\b|wc\b|دورات?\s*مياه|دوره\s*مياه|حمامات|حمام)")
_AREA_RE = re.compile(rf"(?<![/\d])(\d[\d,]*(?:\.\d+)?)\s*{_AREA_UNIT}")
_PER_SQM_RE = re.compile(
    rf"(?:sar|ريال)?\s*({_NUM})\s*(?:sar|ريال)?\s*(?:/\s*|per\s+|لل)(?:sqm|sq\.?\s?m|m2|m²|م²|متر|م\b)"
)
_PRICE_TOKEN_RE = re.compile(rf"(?<![\d.,])({_NUM})(?:\s*({_SUFFIX}))?")
_PRICE_CUE_BEFORE = re.compile(r"(?:price|asking|for|from|sar|ريال|السعر|سعر|بسعر|total|بقيمه)[\s:]*$")
_CURRENCY_AFTER = re.compile(r"^\s*(?:sar|riyals?|ريال|ر\.س)")
_YEARLY = re.compile(r"^\s*(?:/\s*(?:yr|year|y)\b|per year|a year|yearly|annually|annual|سنوي|سنويا|بالسنه)")
_MONTHLY = re.compile(r"^\s*(?:/\s*(?:mo|month|m)\b|per month|a month|monthly|شهري|شهريا|بالشهر)")
_PAYMENTS = re.compile(r"\d+\s*(?:payments|installments|دفعات|دفعتين)|دفعتين")
_PERCENT_RE = re.compile(r"\d+(?:\.\d+)?\s*%")
_YEAR_RE = re.compile(r"\b(?:q[1-4]\s*)?20\d\d\b")

# Numbered multi-listing markers: "1) ... 2) ...", "1- ... 2- ...", "1. ... 2. ..."
_MARKER_RE = re.compile(r"(?:^|(?<=\s))(\d{1,2})\s*[\)\-–\.]\s+")

# Unknown-district capture (only when the gazetteer finds nothing)
_DISTRICT_EN_RE = re.compile(r"\bin\s+((?:Al[\s\-])?[A-Z][a-z]+(?:\s[A-Z][a-z]+)?)")
_DISTRICT_EN2_RE = re.compile(r"((?:Al[\s\-])?[A-Z][a-z]+)\s+district\b")
_DISTRICT_AR_RE = re.compile(r"حي\s+((?:الملك|الامير|الأمير|ام|أم|ابو|أبو)\s+\S+|\S+)")


def _matches_any(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text) for p in patterns)


def classify(folded: str) -> str:
    if _matches_any(_BUYER, folded):
        return "buyer_request"
    if _matches_any(_PROJECT, folded):
        return "project"
    has_type = any(re.search(p, folded) for _, p in _TYPES)
    has_number = re.search(r"\d", folded) is not None
    if _matches_any(_LISTING, folded) or (has_type and has_number):
        return "listing"
    return "irrelevant"


# --- Field helpers (all take folded text; indices map 1:1 to the original) -------

def _property_type(folded: str) -> str | None:
    for name, pattern in _TYPES:
        if name == "land":
            # "450 sqm land" is an area label, not the type.
            for m in re.finditer(pattern, folded):
                if not re.search(rf"{_AREA_UNIT}\s*$", folded[: m.start()].rstrip()):
                    return "land"
            continue
        if re.search(pattern, folded):
            return name
    return None


def _purpose(folded: str) -> str | None:
    sale, rent = re.search(_SALE, folded), re.search(_RENT, folded)
    if sale and rent:
        return None  # contradictory: leave it to review
    return "sale" if sale else "rent" if rent else None


def _single_int(pattern: re.Pattern[str], folded: str) -> int | None:
    values = {int(m.group(1)) for m in pattern.finditer(folded)}
    return values.pop() if len(values) == 1 else None


def _areas(folded: str) -> tuple[float | None, float | None, list[tuple[int, int]]]:
    """Returns (built_up, land, spans). Unlabeled single area → built_up."""
    built, land, unlabeled, spans = [], [], [], []
    for m in _AREA_RE.finditer(folded):
        spans.append(m.span())
        value = float(m.group(1).replace(",", ""))
        after, before = folded[m.end(): m.end() + 15], folded[max(0, m.start() - 15): m.start()]
        if re.search(_LAND_LABEL, after):
            land.append(value)
        elif re.search(_BUA_LABEL, after):
            built.append(value)
        elif re.search(_LAND_LABEL, before):
            land.append(value)
        elif re.search(_BUA_LABEL, before):
            built.append(value)
        else:
            unlabeled.append(value)
    if not built and len(set(unlabeled)) == 1:
        built = unlabeled
    pick = lambda xs: xs[0] if len(set(xs)) == 1 else None  # noqa: E731
    return pick(built), pick(land), spans


def _bedrooms(folded: str) -> int | None:
    n = _single_int(_BEDROOM_RE, folded)
    if n is not None:
        return n
    # Arabic number words without digits: غرفتين = two rooms, غرفة نوم = one bedroom.
    if re.search(r"غرفتين|غرفتان", folded):
        return 2
    if re.search(r"غرفه\s*نوم", folded) and not re.search(r"\d\s*غرف", folded):
        return 1
    return None


def _prices(folded: str, original: str, masked: list[tuple[int, int]]):
    """Returns (price_texts, price_per_sqm_text, period). Raw text, never computed."""
    per_sqm_text = None
    per = list(_PER_SQM_RE.finditer(folded))
    if per:
        per_sqm_text = original[per[0].start(1): per[0].end(1)]
        masked = masked + [m.span() for m in per]

    def is_masked(s: int, e: int) -> bool:
        return any(s < me and e > ms for ms, me in masked)

    candidates: list[tuple[str, int]] = []
    for m in _PRICE_TOKEN_RE.finditer(folded):
        s, e = m.span()
        if is_masked(s, e):
            continue
        has_suffix = m.group(2) is not None
        cue = _PRICE_CUE_BEFORE.search(folded[max(0, s - 12): s])
        currency = _CURRENCY_AFTER.match(folded[e: e + 8])
        period = _YEARLY.match(folded[e: e + 12]) or _MONTHLY.match(folded[e: e + 12])
        digits = re.sub(r"\D", "", m.group(1))
        if not (has_suffix or cue or currency or period) or len(digits) < 3 and not has_suffix:
            continue
        candidates.append((original[s:e].strip(), e))

    # One text per distinct amount ("3.2M" and "3,200,000" are the same price).
    price_texts: list[str] = []
    seen: set = set()
    periods: set[str] = set()
    for text, end in candidates:
        value = parse_amount(text)
        if value is None or value in seen:
            continue
        seen.add(value)
        price_texts.append(text)
        tail = folded[end: end + 12]
        if _YEARLY.match(tail):
            periods.add("yearly")
        elif _MONTHLY.match(tail):
            periods.add("monthly")
    period = periods.pop() if len(periods) == 1 else None
    if period is None and _PAYMENTS.search(folded):
        period = "yearly"  # rent paid in N payments is a yearly rent (PLAN.md §5)
    if period is None and re.search(r"\btotal\b|اجمالي", folded):
        period = "total"
    return price_texts, per_sqm_text, period


def _city(text: str) -> str | None:
    names = {m.place.name for m in find_places(text, CITIES)}
    return names.pop() if len(names) == 1 else None


def _district(text: str, city: str | None) -> str | None:
    known = {m.place.name for m in find_places(text, DISTRICTS)}
    if len(known) == 1:
        return known.pop()
    if known:
        return None  # several districts in one listing: ambiguous
    for pattern in (_DISTRICT_AR_RE, _DISTRICT_EN2_RE, _DISTRICT_EN_RE):
        m = pattern.search(text)
        if not m:
            continue
        name = m.group(1).strip(" ،,.-–")
        if not name or find_places(name, CITIES):
            continue  # "in Riyadh" is a city, not a district
        place = lookup_district(name)
        return place.name if place else name
    return None


def _split_segments(text: str) -> tuple[str, list[str]]:
    """Split a numbered multi-listing message. Returns (header, segments)."""
    markers = [m for m in _MARKER_RE.finditer(text)]
    numbers = [int(m.group(1)) for m in markers]
    if len(markers) >= 2 and numbers == list(range(1, len(markers) + 1)):
        header = text[: markers[0].start()]
        segments = [
            text[m.end(): markers[i + 1].start() if i + 1 < len(markers) else len(text)]
            for i, m in enumerate(markers)
        ]
        return header, segments
    return "", [text]


class RuleExtractor:
    name = "rules"

    def extract(self, message: RawMessage) -> ExtractionCandidate:
        text = message.text
        folded = fold(text)
        classification = classify(folded)
        if classification in ("buyer_request", "irrelevant"):
            return ExtractionCandidate(classification=classification)
        if classification == "project":
            return ExtractionCandidate(classification="project", project=self._project(text, folded))

        header, segments = _split_segments(text)
        phones = find_saudi_mobiles(text)
        shared_phone = phones[0] if phones else None
        shared_city = _city(text)
        shared_purpose = _purpose(folded)
        header_district = _district(header, shared_city) if header else None

        listings = []
        for seg in segments:
            seg_folded = fold(seg)
            masked = [m.span() for m in _BEDROOM_RE.finditer(seg_folded)]
            masked += [m.span() for m in _BATH_RE.finditer(seg_folded)]
            masked += [m.span() for m in _PERCENT_RE.finditer(seg_folded)]
            masked += [m.span() for m in _YEAR_RE.finditer(seg_folded)]
            masked += [m.span() for m in re.finditer(r"(?:\+|00)?\d[\d\s\-]{8,}\d", seg_folded)
                       if find_saudi_mobiles(seg_folded[m.start(): m.end()])]
            built, land, area_spans = _areas(seg_folded)
            price_texts, per_sqm_text, period = _prices(seg_folded, seg, masked + area_spans)
            seg_phones = find_saudi_mobiles(seg)
            listings.append(ListingCandidate(
                purpose=_purpose(seg_folded) or shared_purpose,
                property_type=_property_type(seg_folded),
                city=_city(seg) or shared_city,
                district=_district(seg, shared_city) or header_district,
                bedrooms=_bedrooms(seg_folded),
                bathrooms=_single_int(_BATH_RE, seg_folded),
                built_up_area_sqm=built,
                land_area_sqm=land,
                prices=[PriceMention(quote=t) for t in price_texts],
                price_period=period,
                price_per_sqm=PriceMention(quote=per_sqm_text) if per_sqm_text else None,
                contact_phone_text=seg_phones[0] if seg_phones else shared_phone,
            ))
        return ExtractionCandidate(classification="listing", listings=listings)

    def _project(self, text: str, folded: str) -> ProjectCandidate:
        name = None
        m = re.search(r"(?:new launch|launch|project|مشروع)\s*[:\-–]?\s*"
                      r"((?:[A-Z][\w']*)(?:\s+[A-Z][\w']*){0,4})", text, re.IGNORECASE)
        if m and m.group(1)[0].isupper():
            name = m.group(1).strip()
        min_price = None
        m = re.search(rf"(?:from|starting(?: from| at)?|تبدا من|ابتداء من|يبدا من)\s*(?:sar|ريال)?\s*"
                      rf"({_NUM}(?:\s*{_SUFFIX})?)", folded)
        if m:
            min_price = text[m.start(1): m.end(1)].strip()
        min_bed = max_bed = None
        r = _BEDROOM_RANGE_RE.search(folded)
        if r:
            min_bed, max_bed = int(r.group(1)), int(r.group(2))
        delivery = None
        m = re.search(r"(?:handover|delivery|تسليم)\s*(?:in|:)?\s*((?:q[1-4]\s*)?20\d\d)", folded)
        if m:
            delivery = text[m.start(1): m.end(1)]
        payment = None
        m = re.search(r"\d+\s*%\s*(?:down\s*payment|مقدم|دفعه اولى)|payment plan[^.,]*", folded)
        if m:
            payment = text[m.start(): m.end()].strip()
        phones = find_saudi_mobiles(text)
        return ProjectCandidate(
            name=name,
            city=_city(text),
            district=None,
            min_price=PriceMention(quote=min_price) if min_price else None,
            min_bedrooms=min_bed,
            max_bedrooms=max_bed,
            delivery_date=delivery,
            payment_terms=payment,
            contact_phone_text=phones[0] if phones else None,
        )
