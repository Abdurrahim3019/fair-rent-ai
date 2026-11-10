"""bina.az search results: URL builder and card parser.

Search pages are rendered client-side, so the HTML passed here must come from
a real browser render (selenium_fallback.render_page), not plain requests.
"""
from datetime import datetime
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from pydantic import BaseModel

from src.models import Listing
from src.scraper.normalize import (
    BASE_URL,
    CATEGORIES,
    DISTRICTS,
    parse_float,
    parse_floor,
    parse_int,
    parse_rooms,
    price_period,
)


class SearchCard(BaseModel):
    id: str
    url: str
    price_azn: int | None
    period: str | None  # 'month' / 'day'
    location_label: str | None  # metro / settlement / district label shown on card
    rooms: int | None
    area_m2: float | None
    floor: int | None
    total_floors: int | None
    is_vip: bool


def build_search_url(
    property_type: str, district: str | None = None, rooms: int | None = None
) -> str:
    """e.g. ('mənzil', 'Xətai', 2) -> https://bina.az/baki/xetai/kiraye/menziller?room_ids[]=2"""
    if property_type not in CATEGORIES:
        raise ValueError(f"Unknown property type: {property_type}")
    slug, _ = CATEGORIES[property_type]
    if district:
        if district not in DISTRICTS:
            raise ValueError(f"Unknown district: {district}")
        loc = DISTRICTS[district]
    else:
        loc = "baki"
    url = f"{BASE_URL}/{loc}/kiraye/{slug}"
    params = []
    if rooms is not None and property_type in ("mənzil", "həyət evi", "ofis"):
        params.append(("room_ids[]", "5+" if rooms >= 5 else str(rooms)))
    return url + ("?" + urlencode(params) if params else "")


def _aria_fields(label: str | None) -> dict[str, str]:
    """'rooms,2 otaqlı,area,100 m²,floor,16/16 mərtəbə' -> {'rooms': '2 otaqlı', ...}."""
    if not label:
        return {}
    parts = [p.strip() for p in label.split(",")]
    return {parts[i]: parts[i + 1] for i in range(0, len(parts) - 1, 2)}


def _location_label(card) -> str | None:
    price = card.find(attrs={"data-cy": "item-card-price-full"})
    if not price:
        return None
    block = price.find_parent("div")
    sib = block.find_next_sibling("span") if block else None
    if sib and not sib.find("span"):
        return sib.get_text(strip=True) or None
    return None


def parse_search_cards(html: str) -> list[SearchCard]:
    soup = BeautifulSoup(html, "lxml")
    cards: list[SearchCard] = []
    seen: set[str] = set()
    for card in soup.select('[data-cy="item-card"]'):
        link = card.select_one('a[data-cy="item-card-link"]')
        if not link or not link.get("href", "").startswith("/items/"):
            continue
        item_id = link["href"].rstrip("/").split("/")[-1]
        if item_id in seen:
            continue
        seen.add(item_id)
        aria = _aria_fields(link.get("aria-label"))
        price_el = card.select_one('[data-cy="item-card-price-full"]')
        per_el = card.select_one('[data-cy="item-card-price-container"]')
        floor, total = parse_floor(aria.get("floor"))
        cards.append(
            SearchCard(
                id=item_id,
                url=BASE_URL + link["href"],
                price_azn=parse_int(price_el.get_text()) if price_el else None,
                period=price_period(per_el.get_text()) if per_el else None,
                location_label=_location_label(card),
                rooms=parse_rooms(aria.get("rooms")),
                area_m2=parse_float(aria.get("area")),
                floor=floor,
                total_floors=total,
                is_vip="vip" in (card.get("data-stat") or ""),
            )
        )
    return cards


def card_to_listing(card: SearchCard, property_type: str, district: str) -> Listing | None:
    """Lightweight Listing from a card. Returns None for daily rents / incomplete cards."""
    if card.period != "month" or not card.price_azn or not card.area_m2:
        return None
    metro = card.location_label[:-3].strip() if card.location_label and card.location_label.endswith(" m.") else None
    return Listing(
        id=card.id,
        url=card.url,
        property_type=property_type,
        price_azn=card.price_azn,
        rooms=card.rooms,
        area_m2=card.area_m2,
        floor=card.floor,
        total_floors=card.total_floors,
        district=district,
        metro=metro,
        title=card.location_label or "",
        scraped_at=datetime.now(),
    )
