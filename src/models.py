"""Pydantic data models shared across the app."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

PropertyType = Literal["mənzil", "həyət evi", "ofis", "obyekt"]
VerdictLabel = Literal["ədalətli", "baha", "ucuz", "məlumat azdır"]


class Listing(BaseModel):
    id: str
    url: str
    property_type: PropertyType
    price_azn: int = Field(gt=0, description="Monthly rent in AZN")
    rooms: int | None = None  # often missing for commercial listings
    area_m2: float = Field(gt=0)
    floor: int | None = None
    total_floors: int | None = None
    district: str
    metro: str | None = None
    renovated: bool | None = None
    is_new_building: bool | None = None
    title: str = ""
    scraped_at: datetime = Field(default_factory=datetime.now)

    @property
    def price_per_m2(self) -> float:
        return self.price_azn / self.area_m2


class Comparable(Listing):
    similarity_score: float = Field(ge=0, le=1)
    reason: str = ""


class MarketStats(BaseModel):
    n: int
    median_price: float
    p25: float
    p75: float
    median_price_per_m2: float
    listing_percentile: float | None = None  # where the target sits, 0–100


class Verdict(BaseModel):
    label: VerdictLabel
    diff_azn: int
    diff_pct: float
    fair_range: tuple[int, int]
    user_guess: int | None = None
    explanation: str = ""
    negotiation_message: str = ""


class AreaQuery(BaseModel):
    property_type: PropertyType
    districts: list[str] = Field(min_length=1, max_length=5)
    area_min: float | None = None
    area_max: float | None = None
    rooms: int | None = None  # residential only
    budget_azn: int | None = None


class DistrictSummary(BaseModel):
    district: str
    n: int
    median_price: float | None = None
    p25: float | None = None
    p75: float | None = None
    median_price_per_m2: float | None = None
    within_budget: int = 0
    enough_data: bool = True


class AreaReport(BaseModel):
    query: AreaQuery
    districts: list[DistrictSummary]
    best_value_district: str | None = None
    matching_listings: list[Listing] = []
    summary: str = ""
