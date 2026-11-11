"""Small hand-built city/district dictionary (English + Arabic aliases).

Deliberately small (PLAN.md §14): it covers the sample areas plus a few common ones.
A district that exists in more than one city lists all of them, and is then never used
to infer a city (PLAN.md §5). Names are matched on `fold()`-ed text.
"""

import re
from dataclasses import dataclass

from app.textnorm import to_ascii_digits

_FOLD_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ة": "ه", "ى": "ي"})


def fold(text: str) -> str:
    """Lowercase, ASCII digits, unify Arabic letter variants. Keeps string length 1:1,
    so indices in the folded text map straight back to the original."""
    return to_ascii_digits(text).lower().translate(_FOLD_MAP)


@dataclass(frozen=True)
class Place:
    name: str                  # canonical (English) name used in output
    aliases: tuple[str, ...]   # spellings without the "Al"/"ال" article
    cities: tuple[str, ...] = ()  # for districts: every city that has this district

    @property
    def is_ambiguous(self) -> bool:
        return len(self.cities) != 1


CITIES: tuple[Place, ...] = (
    Place("Riyadh", ("riyadh", "riyad", "رياض")),
    Place("Jeddah", ("jeddah", "jedda", "jiddah", "جده")),
    Place("Dammam", ("dammam", "دمام")),
    Place("Khobar", ("khobar", "خبر")),
    Place("Makkah", ("makkah", "mecca", "مكه")),
    Place("Madinah", ("madinah", "medina", "مدينه المنوره")),
)

DISTRICTS: tuple[Place, ...] = (
    # Riyadh
    Place("Al Malqa", ("malqa", "malga", "ملقا"), ("Riyadh",)),
    Place("Al Narjis", ("narjis", "narjes", "nargis", "نرجس"), ("Riyadh",)),
    Place("Al Yasmin", ("yasmin", "yasmeen", "ياسمين"), ("Riyadh",)),
    Place("Hittin", ("hittin", "hitteen", "hattin", "حطين"), ("Riyadh",)),
    Place("Al Sahafa", ("sahafa", "صحافه"), ("Riyadh",)),
    Place("Al Aqiq", ("aqiq", "عقيق"), ("Riyadh",)),
    Place("Al Qirawan", ("qirawan", "qairawan", "قيروان"), ("Riyadh",)),
    # Jeddah
    Place("Obhur", ("obhur", "abhur", "ابحر"), ("Jeddah",)),
    # Ambiguous: the same name exists in several cities
    Place("Al Olaya", ("olaya", "olaia", "ulaya", "عليا"), ("Riyadh", "Khobar")),
    Place("Al Rawdah", ("rawdah", "rawda", "rawdha", "روضه"), ("Riyadh", "Jeddah")),
    Place("Al Hamra", ("hamra", "حمراء", "حمرا"), ("Riyadh", "Jeddah")),
    Place("Al Shati", ("shati", "shatee", "شاطئ"), ("Jeddah", "Dammam")),
    Place("Al Nakheel", ("nakheel", "نخيل"), ("Riyadh", "Jeddah")),
)

# Arabic *letters* only. The full block (U+0600–06FF) also holds punctuation like the
# Arabic comma "،", which must count as a word boundary (DECISIONS.md D5).
AR_LETTERS = "ء-يٮ-ۓ"


def _alias_pattern(alias: str) -> re.Pattern[str]:
    if re.search(f"[{AR_LETTERS}]", alias):
        # optional article with clitic prefixes: ال / بال / وال / فال / كال / لل
        return re.compile(
            rf"(?<![{AR_LETTERS}])(?:[وبفك]?ال|لل)?{re.escape(alias)}(?![{AR_LETTERS}])"
        )
    return re.compile(rf"(?<![a-z])(?:(?:al|el)[\s\-]?)?{re.escape(alias)}(?![a-z])")


_PATTERNS: dict[Place, list[re.Pattern[str]]] = {
    p: [_alias_pattern(a) for a in p.aliases] for p in CITIES + DISTRICTS
}


@dataclass(frozen=True)
class Match:
    place: Place
    start: int
    end: int


def find_places(text: str, places: tuple[Place, ...]) -> list[Match]:
    """All matches of the given places in `text`, ordered by position."""
    folded = fold(text)
    found = []
    for place in places:
        for pat in _PATTERNS[place]:
            for m in pat.finditer(folded):
                found.append(Match(place, m.start(), m.end()))
    return sorted(found, key=lambda m: m.start)


def lookup_district(name: str) -> Place | None:
    """Exact lookup of a district by any alias (used for pattern-captured names)."""
    matches = find_places(name, DISTRICTS)
    return matches[0].place if matches else None
