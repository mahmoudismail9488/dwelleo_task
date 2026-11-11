"""Pydantic models: input message, extractor candidates, and the output contract (PLAN.md §4).

Two layers on purpose:
- *Candidate* models hold what an extractor (LLM or rules) claims, unnormalized and
  untrusted. Enums are plain strings and prices are raw text, so a bad value can be
  flagged per field instead of failing the whole message.
- *Contract* models hold the final, validated output.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


# --- Input -------------------------------------------------------------------

class RawMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    text: str
    sender: str | None = None
    received_at: str | None = None


# --- Enums -------------------------------------------------------------------

class Classification(StrEnum):
    LISTING = "listing"
    PROJECT = "project"
    BUYER_REQUEST = "buyer_request"
    IRRELEVANT = "irrelevant"


class Purpose(StrEnum):
    SALE = "sale"
    RENT = "rent"


class PropertyType(StrEnum):
    VILLA = "villa"
    APARTMENT = "apartment"
    STUDIO = "studio"
    LAND = "land"
    TOWNHOUSE = "townhouse"
    OTHER = "other"


class PricePeriod(StrEnum):
    TOTAL = "total"
    YEARLY = "yearly"
    MONTHLY = "monthly"


# --- Extractor candidates (untrusted) ------------------------------------------

class ListingCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    purpose: str | None = None
    property_type: str | None = None
    city: str | None = None
    district: str | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    built_up_area_sqm: float | None = None
    land_area_sqm: float | None = None
    price_text: str | None = None          # raw, e.g. "3.2M", "950 ألف", "85,000"
    price_period: str | None = None
    price_per_sqm_text: str | None = None  # raw, e.g. "2,500"
    contact_phone_text: str | None = None  # raw, as written in the message


class ProjectCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    city: str | None = None
    district: str | None = None
    min_price_text: str | None = None
    min_bedrooms: int | None = None
    max_bedrooms: int | None = None
    delivery_date: str | None = None
    payment_terms: str | None = None
    contact_phone_text: str | None = None


class ExtractionCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    classification: str
    listings: list[ListingCandidate] = Field(default_factory=list)
    project: ProjectCandidate | None = None


# --- Output contract -----------------------------------------------------------

class Price(BaseModel):
    amount: int | float | None = None
    currency: Literal["SAR"] = "SAR"
    period: PricePeriod | None = None


class PriceCandidate(BaseModel):
    amount: int | float
    basis: str  # e.g. "stated_total", "computed_from_per_sqm"


class Listing(BaseModel):
    id: str
    purpose: Purpose | None = None
    property_type: PropertyType | None = None
    city: str | None = None
    district: str | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    area_sqm: float | None = None
    land_area_sqm: float | None = None
    price: Price = Field(default_factory=Price)
    price_per_sqm: int | float | None = None
    price_candidates: list[PriceCandidate] = Field(default_factory=list)
    contact_phone: str | None = Field(default=None, pattern=r"^\+9665\d{8}$")
    review_reasons: list[str] = Field(default_factory=list)
    duplicate_of: str | None = None
    inferred_fields: list[str] = Field(default_factory=list)
    source: Literal["llm", "rules"]

    @computed_field
    @property
    def needs_review(self) -> bool:
        # Derived, never set by hand: a listing needs review iff it has a reason.
        return bool(self.review_reasons)


class Project(BaseModel):
    name: str | None = None
    city: str | None = None
    district: str | None = None
    min_price: int | float | None = None
    min_bedrooms: int | None = None
    max_bedrooms: int | None = None
    delivery_date: str | None = None
    payment_terms: str | None = None
    contact_phone: str | None = Field(default=None, pattern=r"^\+9665\d{8}$")


class MessageResult(BaseModel):
    message_id: str
    classification: Classification
    listings: list[Listing] = Field(default_factory=list)
    project: Project | None = None

    @model_validator(mode="after")
    def _shape_matches_classification(self) -> "MessageResult":
        if self.classification != Classification.LISTING and self.listings:
            raise ValueError(f"{self.classification} must have no listings")
        if self.classification != Classification.PROJECT and self.project is not None:
            raise ValueError("only a project message may carry a project object")
        return self
