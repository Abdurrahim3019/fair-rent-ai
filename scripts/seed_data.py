"""Pre-collect listings into the SQLite DB (run locally before deploy).

Usage:
    python -m scripts.seed_data                 # full seed (~150–220 listings)
    python -m scripts.seed_data --scale 0.1     # quick smoke run
    python -m scripts.seed_data --no-enrich     # cards only (faster, no renovation info)
    python -m scripts.seed_data --stats         # only print DB stats
"""
import argparse
import logging
import time

from src import db
from src.models import Listing
from src.scraper.selenium_fallback import chrome_driver
from src.scraper.service import fetch_listing, search_live

log = logging.getLogger("seed")

# property_type -> (target count, districts to sample, pages per district)
PLAN: dict[str, tuple[int, list[str], int]] = {
    "mənzil": (120, ["Nəsimi", "Yasamal", "Nərimanov", "Xətai", "Binəqədi", "Səbail", "Nizami", "Sabunçu"], 1),
    "həyət evi": (30, ["Xəzər", "Sabunçu", "Binəqədi", "Suraxanı", "Abşeron"], 1),
    "ofis": (30, ["Nəsimi", "Yasamal", "Nərimanov", "Səbail", "Xətai"], 1),
    "obyekt": (30, ["Nəsimi", "Yasamal", "Nərimanov", "Xətai", "Səbail", "Binəqədi"], 1),
}


def collect_type(ptype: str, target: int, districts: list[str], pages: int, driver) -> list[Listing]:
    per_district = max(1, -(-target // len(districts)))  # ceil
    out: list[Listing] = []
    for d in districts:
        try:
            found = search_live(ptype, d, pages=pages, driver=driver)
        except Exception as exc:
            log.warning("Search failed %s/%s: %s", ptype, d, exc)
            continue
        new = [x for x in found if x.id not in {o.id for o in out}][:per_district]
        out.extend(new)
        log.info("%-9s %-10s +%d (cəmi %d)", ptype, d, len(new), len(out))
        if len(out) >= target:
            break
    return out[:target]


def enrich(listings: list[Listing]) -> list[Listing]:
    """Replace card-only data with the full listing page (district, renovation, building)."""
    out = []
    for i, x in enumerate(listings, 1):
        try:
            full = fetch_listing(x.url, use_cache=True, save=False)
            out.append(full if full.property_type == x.property_type else x)
        except ValueError as exc:
            log.info("Skip %s: %s", x.id, exc)  # daily / sale / removed listing
        if i % 20 == 0:
            log.info("Enrich %d/%d", i, len(listings))
    return out


def print_stats() -> None:
    rows = db.count_by()
    total = sum(n for *_, n in rows)
    print(f"\nDB-də cəmi elan: {total}  (son yenilənmə: {db.last_updated():%Y-%m-%d %H:%M})")
    current = None
    for ptype, district, n in rows:
        if ptype != current:
            sub = sum(c for p, _, c in rows if p == ptype)
            print(f"\n{ptype} ({sub}):")
            current = ptype
        print(f"  {district:<12} {n}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=float, default=1.0, help="multiply targets (e.g. 0.1)")
    ap.add_argument("--no-enrich", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--types", help="comma-separated property types, e.g. 'ofis,obyekt'")
    ap.add_argument("--pages", type=int, help="override result pages per district (1–3)")
    ap.add_argument("--districts", help="comma-separated districts (override plan)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    db.init_db()
    if args.stats:
        print_stats()
        return

    t0 = time.time()
    collected: list[Listing] = []
    with chrome_driver() as driver:
        types = args.types.split(",") if args.types else list(PLAN)
        for ptype in types:
            target, districts, pages = PLAN[ptype]
            if args.districts:
                districts = args.districts.split(",")
            if args.pages:
                pages = args.pages
                target = 10_000  # take everything found
            target = max(1, round(target * args.scale))
            collected += collect_type(ptype, target, districts, pages, driver)
    log.info("Kartlardan %d elan yığıldı (%.0fs)", len(collected), time.time() - t0)

    # Never re-fetch or overwrite listings that are already fully parsed in the DB.
    collected = [x for x in collected if (old := db.get_listing(x.id)) is None or old.renovated is None]
    log.info("Yeni elan: %d", len(collected))
    if not args.no_enrich:
        collected = enrich(collected)
    db.upsert_listings(collected)
    log.info("DB-yə yazıldı: %d elan (%.0fs)", len(collected), time.time() - t0)
    print_stats()


if __name__ == "__main__":
    main()
