from typing import Protocol

from app.models import ExtractionCandidate, RawMessage


class Extractor(Protocol):
    """Turns a raw message into an *untrusted* candidate.

    Extractors report only what the message states. No inference, no review flags:
    that's the shared pipeline's job (CLAUDE.md, Architecture).
    """

    name: str

    def extract(self, message: RawMessage) -> ExtractionCandidate: ...
