"""High-level scraping API used by tools/scripts: cache -> live -> fallback."""
import logging

from src import db
from src.config import MAX_PRICE_PER_M2, MAX_SEARCH_PAGES, MIN_PRICE_PER_M2
from src.models import Listing
from src.scraper.http import FetchError, fetch_html
from src.scraper.listing_parser import ParseError, parse_listing
from src.scraper.normalize import BASE_URL, is_listing_url, item_id_from_url
from src.scraper.search_parser import build_search_url, card_to_listing, parse_search_cards

log = logging.getLogger(__name__)


def is_plausible(x: Listing) -> bool:
    return MIN_PRICE_PER_M2 <= x.price_per_m2 <= MAX_PRICE_PER_M2


def fetch_listing(url: str, use_cache: bool = True, save: bool = True, engine=None) -> Listing:
    """Fetch and parse one listing page. Raises ValueError with an Azerbaijani message."""
    url = url.strip()
    if not is_listing_url(url):
        raise ValueError("Link düzgün deyil. Nümunə: https://bina.az/items/1234567")
    item_id = item_id_from_url(url)
    canonical = f"{BASE_URL}/items/{item_id}"
    if use_cache:
        cached = db.get_listing(item_id, engine=engine)
        if cached is not None and cached.renovated is not None:  # full (not card-only) record
            return cached
    try:
        html = fetch_html(canonical, use_cache=use_cache, engine=engine)
    except FetchError as exc:
        raise ValueError(f"Elan səhifəsi açılmadı: {exc}") from exc
    try:
        listing = parse_listing(html, canonical, item_id=item_id)
    except ParseError as exc:
        raise ValueError(str(exc)) from exc
    if save:
        db.upsert_listings([listing], engine=engine)
    return listing


def search_live(
    property_type: str,
    district: str,
    rooms: int | None = None,
    pages: int = 1,
    driver=None,
    engine=None,
) -> list[Listing]:
    """Render a search page in headless Chrome and convert cards to Listings."""
    from src.scraper.selenium_fallback import render_page

    pages = max(1, min(pages, MAX_SEARCH_PAGES))
    url = build_search_url(property_type, district, rooms)
    cache_key = f"{url}#pages={pages}"
    html = db.get_cached_page(cache_key, engine=engine)
    if html is None:
        html = render_page(url, driver=driver, pages=pages)
        db.put_cached_page(cache_key, html, engine=engine)
    out = []
    for card in parse_search_cards(html):
        if card.is_vip:  # VIP blocks are not guaranteed to match the district filter
            continue
        x = card_to_listing(card, property_type, district)
        if x and is_plausible(x):
            out.append(x)
    return out


def search_comparables(
    property_type: str,
    district: str,
    rooms: int | None = None,
    area_min: float | None = None,
    area_max: float | None = None,
    max_pages: int = 1,
    live: bool = True,
    exclude_id: str | None = None,
    engine=None,
) -> list[Listing]:
    """DB cache first; if fewer than 8 results and live allowed, scrape and merge."""
    found = db.query_listings(property_type, [district], rooms, area_min, area_max, exclude_id, engine=engine)
    if len(found) >= 8 or not live:
        return found
    try:
        fresh = search_live(property_type, district, rooms, max_pages, engine=engine)
        db.upsert_listings(
            [x for x in fresh if db.get_listing(x.id, engine=engine) is None], engine=engine
        )
    except Exception as exc:  # selenium missing / blocked -> continue with DB data
        log.warning("Live search failed, using DB only: %s", exc)
    return db.query_listings(property_type, [district], rooms, area_min, area_max, exclude_id, engine=engine)
