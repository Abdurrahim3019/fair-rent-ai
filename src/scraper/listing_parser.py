"""Parse a single bina.az listing page (server-rendered) into a Listing.

Strategy: JSON-LD (schema.org Product) first, then data-cy HTML attributes.
Every field extractor returns None when not found instead of raising.
"""
import json
from datetime import datetime

from bs4 import BeautifulSoup

from src.models import Listing
from src.scraper.normalize import (
    BASE_URL,
    SLUG_TO_TYPE,
    normalize_district,
    parse_float,
    parse_floor,
    parse_int,
    parse_rooms,
    price_period,
)


class ParseError(ValueError):
    pass


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _text(soup: BeautifulSoup, cy: str) -> str | None:
    el = soup.find(attrs={"data-cy": cy})
    return el.get_text(" ", strip=True) if el else None


def _ld_product(soup: BeautifulSoup) -> dict | None:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Product":
            return data
    return None


def _sizes(soup: BeautifulSoup) -> dict[str, str]:
    """sizes-section pairs: {'Sahə': '100 m²', 'mərtəbə': '3 / 7', 'Ofis': '4 otaqlı', ...}."""
    out: dict[str, str] = {}
    sec = soup.find(attrs={"data-cy": "sizes-section"})
    if not sec:
        return out
    for val in sec.find_all(attrs={"data-cy": "sizes-section__val"}):
        desc = val.find_next_sibling(attrs={"data-cy": "sizes-section__desc"})
        if desc:
            out[desc.get_text(strip=True)] = val.get_text(" ", strip=True)
    return out


def _location_links(soup: BeautifulSoup) -> list[tuple[str, str]]:
    sec = soup.find(attrs={"data-cy": "cur-location__locations"})
    if not sec:
        return []
    return [
        (a.get_text(strip=True), a.get("href", ""))
        for a in sec.find_all(attrs={"data-cy": "cur-location__link"})
    ]


# --- field extractors ---

def parse_price(soup: BeautifulSoup) -> tuple[int | None, str | None]:
    """Return (price, period) where period is 'month' / 'day' / None."""
    text = _text(soup, "price-section")
    price = parse_int(text.split("AZN")[0]) if text and "AZN" in text else parse_int(text)
    period = price_period(text)
    if price is None:
        ld = _ld_product(soup) or {}
        price = parse_int(str(ld.get("offers", {}).get("price", "")))
    return price, period


def parse_area(soup: BeautifulSoup) -> float | None:
    area = parse_float(_sizes(soup).get("Sahə"))
    if area is None:
        ld = _ld_product(soup) or {}
        v = ld.get("offers", {}).get("itemOffered", {}).get("floorSize", {}).get("value")
        area = float(v) if v is not None else None
    return area


def parse_rooms_field(soup: BeautifulSoup) -> int | None:
    ld = _ld_product(soup) or {}
    rooms = ld.get("offers", {}).get("itemOffered", {}).get("numberOfRooms")
    if isinstance(rooms, int):
        return rooms
    for v in _sizes(soup).values():
        r = parse_rooms(v)
        if r is not None:
            return r
    return None


def parse_floor_field(soup: BeautifulSoup) -> tuple[int | None, int | None]:
    return parse_floor(_sizes(soup).get("mərtəbə"))


def parse_property_type(soup: BeautifulSoup) -> str | None:
    # Location links look like /baki/nerimanov/kiraye/ofisler[/...]
    for _, href in _location_links(soup):
        parts = href.strip("/").split("/")
        if "kiraye" in parts:
            i = parts.index("kiraye")
            if i + 1 < len(parts) and parts[i + 1] in SLUG_TO_TYPE:
                return SLUG_TO_TYPE[parts[i + 1]]
    crumbs = (_text(soup, "current-item-breadcrumbs") or "").lower()
    if "ofis" in crumbs:
        return "ofis"
    if "obyekt" in crumbs:
        return "obyekt"
    if "həyət" in crumbs or "bağ evi" in crumbs:
        return "həyət evi"
    if "tikili" in crumbs or "mənzil" in crumbs:
        return "mənzil"
    return None


def parse_is_lease(soup: BeautifulSoup) -> bool:
    crumbs = _text(soup, "current-item-breadcrumbs") or ""
    title = _text(soup, "current-item-title") or ""
    return "Kirayə" in crumbs or "İcarəyə" in title


def parse_district(soup: BeautifulSoup) -> str | None:
    for label, _ in _location_links(soup):
        d = normalize_district(label)
        if d:
            return d
    return None


def parse_metro(soup: BeautifulSoup) -> str | None:
    for label, _ in _location_links(soup):
        if label.endswith(" m."):
            return label[:-3].strip()
    return None


def parse_renovated(soup: BeautifulSoup) -> bool | None:
    """bina.az shows a 'Təmir' badge only for renovated listings; no badge -> not renovated."""
    if soup.find(attrs={"data-cy": "price-section"}) is None:
        return None  # not a real listing page, can't tell
    sec = soup.find(attrs={"data-cy": "services-section"})
    if not sec:
        return False
    items = [i.get_text(strip=True).lower() for i in sec.find_all(attrs={"data-cy": "services-section__item"})]
    return any(i in ("təmir", "təmirli") for i in items)


def parse_is_new_building(soup: BeautifulSoup) -> bool | None:
    sizes = _sizes(soup)
    joined = " ".join(list(sizes.keys()) + list(sizes.values())).lower()
    if "yeni tikili" in joined:
        return True
    if "köhnə tikili" in joined:
        return False
    return None


def parse_title(soup: BeautifulSoup) -> str:
    t = _text(soup, "current-item-title")
    if t:
        return " ".join(t.split())
    return soup.title.get_text(strip=True) if soup.title else ""


def parse_listing(html: str, url: str, item_id: str | None = None) -> Listing:
    """Parse listing HTML. Raises ParseError if it is not a usable monthly rent listing."""
    soup = _soup(html)
    price, period = parse_price(soup)
    area = parse_area(soup)
    ptype = parse_property_type(soup)
    district = parse_district(soup)

    if not parse_is_lease(soup):
        raise ParseError("Bu elan kirayə deyil (satış elanıdır).")
    if period == "day":
        raise ParseError("Bu, günlük kirayə elanıdır. Yalnız aylıq kirayələr dəstəklənir.")
    missing = [n for n, v in [("qiymət", price), ("sahə", area), ("əmlak növü", ptype), ("rayon", district)] if v is None]
    if missing:
        raise ParseError(f"Elandan bu sahələr oxunmadı: {', '.join(missing)}")

    floor, total = parse_floor_field(soup)
    if item_id is None:
        id_text = _text(soup, "item-id")
        item_id = str(parse_int(id_text)) if id_text else url.rstrip("/").split("/")[-1]
    return Listing(
        id=item_id,
        url=url if url.startswith("http") else BASE_URL + url,
        property_type=ptype,
        price_azn=price,
        rooms=parse_rooms_field(soup),
        area_m2=area,
        floor=floor,
        total_floors=total,
        district=district,
        metro=parse_metro(soup),
        renovated=parse_renovated(soup),
        is_new_building=parse_is_new_building(soup) if ptype == "mənzil" else None,
        title=parse_title(soup),
        scraped_at=datetime.now(),
    )
