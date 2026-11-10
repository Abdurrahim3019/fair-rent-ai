"""LangChain tools for the research agent. Each tool returns compact JSON and
never raises: errors come back as {"error": "..."} so the agent can react."""
import json
import logging

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src import db
from src.comparables import dedupe
from src.config import MAX_SEARCH_PAGES
from src.models import AreaQuery, Listing, PropertyType
from src.scraper import service
from src.scraper.normalize import DISTRICTS, NEIGHBOR_DISTRICTS
from src.stats import compare_districts as _compare_districts
from src.stats import compute_market_stats as _compute_market_stats

log = logging.getLogger(__name__)

# Live scraping can be turned off (tests, offline demo, blocked server IP).
LIVE_SEARCH = {"enabled": False}


def _brief(x: Listing) -> dict:
    """Short listing representation to keep agent context (and cost) small."""
    d = {"id": x.id, "price": x.price_azn, "m2": x.area_m2, "district": x.district}
    if x.rooms is not None:
        d["rooms"] = x.rooms
    if x.floor is not None:
        d["floor"] = f"{x.floor}/{x.total_floors}"
    if x.metro:
        d["metro"] = x.metro
    if x.renovated is not None:
        d["renovated"] = x.renovated
    return d


def _err(msg: str) -> str:
    return json.dumps({"error": msg}, ensure_ascii=False)


# --- schemas ---

class FetchListingArgs(BaseModel):
    url: str = Field(description="bina.az listing URL, e.g. https://bina.az/items/1234567")


class SearchArgs(BaseModel):
    property_type: PropertyType = Field(description="Must equal the target listing's property_type")
    district: str = Field(description=f"Baku district, one of: {', '.join(DISTRICTS)}")
    rooms: int | None = Field(None, description="Room count; ignored for ofis/obyekt")
    area_min: float | None = Field(None, description="Minimum area in m²")
    area_max: float | None = Field(None, description="Maximum area in m²")
    max_pages: int = Field(1, ge=1, le=MAX_SEARCH_PAGES, description="Result pages to read (1–3)")


class WidenArgs(BaseModel):
    listing_id: str = Field(description="Target listing id")
    reason: str = Field(description="Why widening is needed, e.g. 'only 5 results'")


class StatsArgs(BaseModel):
    prices: list[float] = Field(description="Monthly prices of comparables (AZN)")
    areas: list[float] = Field(description="Areas of the same comparables (m²), same order")
    target_price: float | None = Field(None, description="Target listing price")


class CompareDistrictsArgs(BaseModel):
    property_type: PropertyType
    districts: list[str] = Field(min_length=1, max_length=5)
    area_min: float | None = None
    area_max: float | None = None
    budget_azn: int | None = None


# --- tools ---

@tool("fetch_listing", args_schema=FetchListingArgs)
def fetch_listing_tool(url: str) -> str:
    """Read one bina.az rental listing and return its fields (price, area, rooms, district...)."""
    try:
        return service.fetch_listing(url).model_dump_json(exclude={"scraped_at"})
    except Exception as exc:
        return _err(str(exc))


@tool("search_comparables", args_schema=SearchArgs)
def search_comparables_tool(
    property_type: str,
    district: str,
    rooms: int | None = None,
    area_min: float | None = None,
    area_max: float | None = None,
    max_pages: int = 1,
) -> str:
    """Find rental listings of the same property type in a district (cache first, then live)."""
    if district not in DISTRICTS:
        return _err(f"Naməlum rayon: {district}. Mümkün: {', '.join(DISTRICTS)}")
    try:
        found = service.search_comparables(
            property_type, district, rooms if property_type not in ("ofis", "obyekt") else None,
            area_min, area_max, max_pages, live=LIVE_SEARCH["enabled"],
        )
        found = dedupe(found)
        return json.dumps({"count": len(found), "listings": [_brief(x) for x in found[:25]]}, ensure_ascii=False)
    except Exception as exc:
        return _err(f"Axtarış alınmadı: {exc}")


@tool("widen_search", args_schema=WidenArgs)
def widen_search_tool(listing_id: str, reason: str) -> str:
    """Use when fewer than 8 comparables were found: searches neighbouring districts and area ±30%."""
    target = db.get_listing(listing_id)
    if target is None:
        return _err(f"Elan tapılmadı: {listing_id}")
    neighbors = NEIGHBOR_DISTRICTS.get(target.district, [])
    found = db.query_listings(
        property_type=target.property_type,
        districts=[target.district, *neighbors],
        area_min=target.area_m2 * 0.7,
        area_max=target.area_m2 * 1.3,
        exclude_id=target.id,
    )
    found = dedupe(found)
    return json.dumps(
        {"widened_to": [target.district, *neighbors], "area_range": [round(target.area_m2 * 0.7), round(target.area_m2 * 1.3)],
         "count": len(found), "listings": [_brief(x) for x in found[:25]]},
        ensure_ascii=False,
    )


@tool("compute_market_stats", args_schema=StatsArgs)
def compute_market_stats_tool(prices: list[float], areas: list[float], target_price: float | None = None) -> str:
    """Compute median, p25, p75, median price per m² and the target's percentile."""
    try:
        return _compute_market_stats(prices, areas, target_price).model_dump_json()
    except Exception as exc:
        return _err(str(exc))


@tool("compare_districts", args_schema=CompareDistrictsArgs)
def compare_districts_tool(
    property_type: str, districts: list[str], area_min: float | None = None,
    area_max: float | None = None, budget_azn: int | None = None,
) -> str:
    """Compare districts for one property type: median price, price per m², listings within budget."""
    try:
        q = AreaQuery(property_type=property_type, districts=districts, area_min=area_min,
                      area_max=area_max, budget_azn=budget_azn)
        data = {d: db.query_listings(property_type=property_type, districts=[d]) for d in districts}
        summaries, best = _compare_districts(q, data)
        return json.dumps({"districts": [s.model_dump() for s in summaries], "best_value_district": best}, ensure_ascii=False)
    except Exception as exc:
        return _err(str(exc))


RESEARCH_TOOLS = [search_comparables_tool, widen_search_tool, compute_market_stats_tool]
ALL_TOOLS = [fetch_listing_tool, *RESEARCH_TOOLS, compare_districts_tool]
