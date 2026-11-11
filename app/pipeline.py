"""process_message(): the single entry point used by both the CLI and the API.

extract → (fallback on failure) → ground → build + review → dedup → store
"""

import logging

from app.dedup import apply_dedup
from app.extractors.base import Extractor
from app.extractors.rules import RuleExtractor
from app.grounding import ground
from app.models import (
    Classification,
    ExtractionCandidate,
    MessageResult,
    ProjectCandidate,
    RawMessage,
)
from app.review import build_listing, build_project
from app.store import Store

log = logging.getLogger(__name__)

_VALID_CLASSES = {c.value for c in Classification}


def _usable(c: ExtractionCandidate) -> bool:
    """A candidate the pipeline can trust structurally (field values are checked later)."""
    if c.classification not in _VALID_CLASSES:
        return False
    if c.classification == "listing" and not c.listings:
        return False
    return True


def _listing_count(c: ExtractionCandidate) -> int:
    return len(c.listings) if c.classification == "listing" else 0


def _extract(message: RawMessage, extractor: Extractor, fallback: Extractor):
    """Run the extractor; on any failure, use the fallback and say so.

    When the primary extractor succeeds, the (free, instant) fallback still runs as a
    cross-check: a different listing count means the LLM may have split or merged
    listings, so every listing is flagged `llm_listing_count_suspicious`.
    """
    if extractor is fallback:
        return fallback.extract(message), fallback.name, []
    rules = fallback.extract(message)
    try:
        candidate = extractor.extract(message)
        if _usable(candidate):
            extra = []
            if candidate.classification == "listing" and _listing_count(candidate) != _listing_count(rules):
                extra.append("llm_listing_count_suspicious")
            return candidate, extractor.name, extra
        log.warning("message %s: %s output unusable, falling back", message.id, extractor.name)
    except Exception as exc:  # timeout, API error, malformed JSON, schema error…
        log.warning("message %s: %s failed (%s), falling back", message.id, extractor.name, exc)
    return rules, fallback.name, ["llm_fallback_used"]


def process_message(
    message: RawMessage,
    store: Store,
    extractor: Extractor | None = None,
    fallback: Extractor | None = None,
) -> MessageResult:
    """Process one message. Idempotent: a message id already stored returns its stored result."""
    existing = store.get_result(message.id)
    if existing is not None:
        return existing

    fallback = fallback or RuleExtractor()
    extractor = extractor or fallback
    candidate, source, extra = _extract(message, extractor, fallback)
    candidate, listing_reasons, project_reasons = ground(candidate, message.text, message.sender)

    classification = candidate.classification
    listings, project = [], None
    if classification == "listing":
        for n, (lc, reasons) in enumerate(zip(candidate.listings, listing_reasons), start=1):
            listing = build_listing(lc, f"{message.id}-{n}", source, message.sender, extra + reasons)
            listings.append(apply_dedup(listing, store.dedup_candidates(listing, message.id)))
    elif classification == "project":
        project = build_project(candidate.project or ProjectCandidate(), extra + project_reasons)

    result = MessageResult(
        message_id=message.id, classification=classification, listings=listings, project=project
    )
    store.save(message, result)
    return result
