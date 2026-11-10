"""Shared text normalization helpers and bina.az reference data."""
import re

BASE_URL = "https://bina.az"

# property_type -> (url slug, bina.az categoryId)
CATEGORIES: dict[str, tuple[str, str]] = {
    "mənzil": ("menziller", "1"),
    "həyət evi": ("heyet-evleri", "5"),
    "ofis": ("ofisler", "7"),
    "obyekt": ("obyektler", "10"),
}
SLUG_TO_TYPE = {slug: t for t, (slug, _) in CATEGORIES.items()}

# Baku districts (from bina.az regions list): name -> location slug.
DISTRICTS: dict[str, str] = {
    "Abşeron": "baki/abseron",
    "Binəqədi": "baki/bineqedi",
    "Xətai": "baki/xetai",
    "Xəzər": "baki/xezer",
    "Qaradağ": "baki/qaradag",
    "Nərimanov": "baki/nerimanov",
    "Nəsimi": "baki/nesimi",
    "Nizami": "baki/nizami",
    "Pirallahı": "baki/abseron/pirallahi",
    "Sabunçu": "baki/sabuncu",
    "Səbail": "baki/sebail",
    "Suraxanı": "baki/suraxani",
    "Yasamal": "baki/yasamal",
}

# Rough adjacency used by widen_search.
NEIGHBOR_DISTRICTS: dict[str, list[str]] = {
    "Nəsimi": ["Yasamal", "Səbail", "Nərimanov"],
    "Yasamal": ["Nəsimi", "Səbail", "Binəqədi"],
    "Səbail": ["Nəsimi", "Yasamal"],
    "Nərimanov": ["Nəsimi", "Nizami", "Xətai", "Binəqədi"],
    "Nizami": ["Nərimanov", "Xətai", "Sabunçu"],
    "Xətai": ["Nizami", "Nərimanov", "Suraxanı"],
    "Binəqədi": ["Yasamal", "Nərimanov", "Sabunçu"],
    "Sabunçu": ["Binəqədi", "Nizami", "Suraxanı"],
    "Suraxanı": ["Sabunçu", "Xətai", "Xəzər"],
    "Xəzər": ["Suraxanı"],
    "Qaradağ": ["Səbail"],
    "Abşeron": ["Binəqədi"],
    "Pirallahı": ["Xəzər"],
}


def parse_int(text: str | None) -> int | None:
    """'2 500 AZN' -> 2500."""
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def parse_float(text: str | None) -> float | None:
    """'130.5 m²' / '130,5 m²' -> 130.5."""
    if not text:
        return None
    m = re.search(r"\d+(?:[.,]\d+)?", text.replace("\xa0", " ").replace(" ", ""))
    return float(m.group().replace(",", ".")) if m else None


def parse_rooms(text: str | None) -> int | None:
    """'4 otaqlı' -> 4."""
    if not text:
        return None
    m = re.search(r"(\d+)\s*otaq", text)
    return int(m.group(1)) if m else None


def parse_floor(text: str | None) -> tuple[int | None, int | None]:
    """'3 / 7' or '8/17 mərtəbə' -> (3, 7)."""
    if not text:
        return None, None
    m = re.search(r"(-?\d+)\s*/\s*(\d+)", text)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def price_period(text: str | None) -> str | None:
    """Return 'month', 'day' or None from 'AZN/ay', '/gün'."""
    if not text:
        return None
    t = text.lower()
    if "/ay" in t:
        return "month"
    if "/gün" in t or "/gun" in t:
        return "day"
    return None


def normalize_district(text: str | None) -> str | None:
    """'Nərimanov r.' -> 'Nərimanov'. Returns None for non-district labels."""
    if not text:
        return None
    t = text.strip()
    if not t.endswith(" r."):
        return None
    name = t[:-3].strip()
    return name if name in DISTRICTS else name


def item_id_from_url(url: str) -> str | None:
    m = re.search(r"/items/(\d+)", url)
    return m.group(1) if m else None


def is_listing_url(url: str) -> bool:
    return bool(re.match(r"^https?://(www\.|ru\.)?bina\.az/items/\d+/?(\?.*)?$", url.strip()))
