"""Fallback: import manually collected listings from CSV, or export DB to CSV.

CSV columns (header required; optional ones may be empty):
    id,url,property_type,price_azn,rooms,area_m2,floor,total_floors,district,metro,renovated,is_new_building,title

Usage:
    python -m scripts.import_csv data/listings.csv
    python -m scripts.import_csv --export data/listings.csv
"""
import argparse
import csv
from datetime import datetime

from pydantic import ValidationError

from src import db
from src.models import Listing

FIELDS = ["id", "url", "property_type", "price_azn", "rooms", "area_m2", "floor",
          "total_floors", "district", "metro", "renovated", "is_new_building", "title"]
BOOL = {"1": True, "true": True, "bəli": True, "he": True, "hə": True,
        "0": False, "false": False, "xeyr": False, "yox": False}


def _clean(row: dict) -> dict:
    out = {}
    for k in FIELDS:
        v = (row.get(k) or "").strip()
        if v == "":
            out[k] = None
        elif k in ("renovated", "is_new_building"):
            out[k] = BOOL.get(v.lower())
        else:
            out[k] = v
    out["title"] = out["title"] or ""
    out["scraped_at"] = datetime.now()
    return out


def import_csv(path: str, engine=None) -> tuple[int, list[str]]:
    ok, errors = [], []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            try:
                ok.append(Listing.model_validate(_clean(row)))
            except ValidationError as exc:
                errors.append(f"sətir {i}: {exc.errors()[0]['loc']} {exc.errors()[0]['msg']}")
    db.upsert_listings(ok, engine=engine)
    return len(ok), errors


def export_csv(path: str, engine=None) -> int:
    rows = db.query_listings(engine=engine)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for x in rows:
            w.writerow({k: getattr(x, k) for k in FIELDS})
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--export", action="store_true")
    args = ap.parse_args()
    db.init_db()
    if args.export:
        print(f"İxrac edildi: {export_csv(args.path)} elan → {args.path}")
        return
    n, errors = import_csv(args.path)
    print(f"İdxal edildi: {n} elan")
    for e in errors:
        print("  Xəta:", e)


if __name__ == "__main__":
    main()
