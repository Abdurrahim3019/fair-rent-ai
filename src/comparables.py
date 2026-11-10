"""Deterministic comparable selection and similarity scoring (no LLM).

Used directly by the stage-4 demo and later wrapped as agent tools.
"""
import logging

from src import db
from src.config import COMMERCIAL_TYPES, TARGET_COMPARABLES
from src.models import Comparable, Listing
from src.scraper.normalize import NEIGHBOR_DISTRICTS

log = logging.getLogger(__name__)

AREA_TOLERANCE = 0.30
MAX_COMPARABLES = 15


def similarity(target: Listing, other: Listing) -> tuple[float, str]:
    """Score 0–1 plus a short Azerbaijani reason. Weights differ for commercial types."""
    reasons: list[str] = []
    area_diff = abs(other.area_m2 - target.area_m2) / target.area_m2
    area_score = max(0.0, 1 - area_diff / 0.5)
    if area_diff <= 0.15:
        reasons.append("sahə yaxındır")

    same_district = other.district == target.district
    district_score = 1.0 if same_district else 0.5
    reasons.append("eyni rayon" if same_district else "qonşu rayon")

    metro_score = 1.0 if target.metro and other.metro == target.metro else 0.0
    if metro_score:
        reasons.append("eyni metro")

    reno_score = 1.0 if target.renovated is None or other.renovated == target.renovated else 0.0
    if target.renovated is not None and other.renovated == target.renovated:
        reasons.append("təmirli" if target.renovated else "təmirsiz")

    if target.property_type in COMMERCIAL_TYPES:
        same_floor_class = (target.floor == 1) == (other.floor == 1) if target.floor and other.floor else True
        floor_score = 1.0 if same_floor_class else 0.3
        score = 0.40 * area_score + 0.25 * district_score + 0.15 * metro_score + 0.10 * reno_score + 0.10 * floor_score
    else:
        rooms_score = 1.0 if other.rooms == target.rooms else 0.4 if other.rooms and target.rooms and abs(other.rooms - target.rooms) == 1 else 0.0
        if rooms_score == 1.0:
            reasons.append("otaq sayı eynidir")
        new_score = 1.0 if target.is_new_building is None or other.is_new_building == target.is_new_building else 0.3
        score = 0.25 * area_score + 0.25 * rooms_score + 0.20 * district_score + 0.10 * metro_score + 0.10 * reno_score + 0.10 * new_score
    return round(score, 3), ", ".join(reasons)


def dedupe(listings: list[Listing]) -> list[Listing]:
    """Drop re-posted duplicates (same price, area, rooms, district, floor)."""
    seen, out = set(), []
    for x in listings:
        key = (x.price_azn, x.area_m2, x.rooms, x.district, x.floor)
        if key not in seen:
            seen.add(key)
            out.append(x)
    return out


def _candidates(target: Listing, districts: list[str], area_tol: float, use_rooms: bool, engine=None) -> list[Listing]:
    rooms = target.rooms if use_rooms and target.property_type not in COMMERCIAL_TYPES else None
    return db.query_listings(
        property_type=target.property_type,
        districts=districts,
        rooms=rooms,
        area_min=target.area_m2 * (1 - area_tol),
        area_max=target.area_m2 * (1 + area_tol),
        exclude_id=target.id,
        engine=engine,
    )


def find_comparables(
    target: Listing, live: bool = False, engine=None, steps: list[str] | None = None
) -> list[Comparable]:
    """Progressively widen the search until TARGET_COMPARABLES are found."""
    log_step = steps.append if steps is not None else (lambda _m: None)
    neighbors = NEIGHBOR_DISTRICTS.get(target.district, [])
    plan = [
        ([target.district], AREA_TOLERANCE, True, "eyni rayon, eyni otaq sayı, sahə ±30%"),
        ([target.district], AREA_TOLERANCE, False, "eyni rayon, sahə ±30%"),
        ([target.district, *neighbors], AREA_TOLERANCE, False, "qonşu rayonlar əlavə edildi"),
        ([target.district, *neighbors], 0.5, False, "sahə aralığı ±50%-ə genişləndi"),
    ]
    if live:
        from src.scraper.service import search_comparables

        try:
            search_comparables(target.property_type, target.district, target.rooms, live=True, engine=engine)
            log_step("bina.az-dan yeni elanlar yoxlanıldı")
        except Exception as exc:
            log.warning("Live search failed: %s", exc)

    found: list[Listing] = []
    for districts, tol, use_rooms, label in plan:
        found = _candidates(target, districts, tol, use_rooms, engine)
        log_step(f"{label}: {len(found)} elan")
        if len(found) >= TARGET_COMPARABLES:
            break

    found = dedupe(found)
    scored = [Comparable(**x.model_dump(), similarity_score=s, reason=r) for x in found for s, r in [similarity(target, x)]]
    scored.sort(key=lambda c: c.similarity_score, reverse=True)
    return scored[:MAX_COMPARABLES]
