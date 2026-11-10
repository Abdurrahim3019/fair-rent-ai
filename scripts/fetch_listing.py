"""CLI: python -m scripts.fetch_listing https://bina.az/items/1234567"""
import sys

from src.scraper.service import fetch_listing


def main() -> None:
    if len(sys.argv) != 2:
        print("İstifadə: python -m scripts.fetch_listing <bina.az elan linki>")
        sys.exit(1)
    try:
        listing = fetch_listing(sys.argv[1])
    except ValueError as exc:
        print(f"Xəta: {exc}")
        sys.exit(2)
    print(listing.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
