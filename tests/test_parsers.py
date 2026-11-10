from pathlib import Path

import pytest
from sqlalchemy import create_engine

from src import db
from src.scraper import service
from src.scraper.listing_parser import ParseError, parse_listing
from src.scraper.normalize import is_listing_url, parse_floor, parse_float, parse_int
from src.scraper.search_parser import build_search_url, card_to_listing, parse_search_cards

FIX = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# (file, id, type, price, rooms, area, floor, total_floors, district, metro, new_building)
LISTINGS = [
    ("listing_menzil_6590355.html", "6590355", "mənzil", 850, 3, 130.0, 1, 10, "Binəqədi", "Nəsimi", True),
    ("listing_menzil_6500573.html", "6500573", "mənzil", 3000, 5, 220.0, 3, 7, "Xətai", None, True),
    ("listing_heyet_evi_5963426.html", "5963426", "həyət evi", 1000, 4, 240.0, None, None, "Xəzər", None, None),
    ("listing_ofis_6603059.html", "6603059", "ofis", 2500, 4, 100.0, None, None, "Nərimanov", "Nəriman Nərimanov", None),
    ("listing_ofis_5508876.html", "5508876", "ofis", 2000, 5, 110.0, None, None, "Yasamal", "İçəri Şəhər", None),
    ("listing_obyekt_6534564.html", "6534564", "obyekt", 2000, None, 65.0, None, None, "Nərimanov", "Nəriman Nərimanov", None),
    ("listing_obyekt_6447444.html", "6447444", "obyekt", 1300, None, 150.0, None, None, "Nəsimi", "Nizami", None),
]


@pytest.mark.parametrize("fname,iid,ptype,price,rooms,area,floor,total,district,metro,new", LISTINGS)
def test_parse_listing(fname, iid, ptype, price, rooms, area, floor, total, district, metro, new):
    x = parse_listing(load(fname), f"https://bina.az/items/{iid}")
    assert x.id == iid
    assert x.property_type == ptype
    assert x.price_azn == price
    assert x.rooms == rooms
    assert x.area_m2 == area
    assert (x.floor, x.total_floors) == (floor, total)
    assert x.district == district
    assert x.metro == metro
    assert x.is_new_building == new
    assert x.renovated is True
    assert x.title.startswith("İcarəyə verilir")


def test_daily_rent_rejected():
    with pytest.raises(ParseError, match="günlük"):
        parse_listing(load("listing_gunluk_3977376.html"), "https://bina.az/items/3977376")


def test_garbage_html_rejected_without_crash():
    with pytest.raises(ParseError):
        parse_listing("<html><body>nothing</body></html>", "https://bina.az/items/1")


@pytest.mark.parametrize(
    "fname", ["search_menziller.html", "search_ofisler.html", "search_obyektler.html", "search_heyet-evleri.html"]
)
def test_parse_search_cards(fname):
    cards = parse_search_cards(load(fname))
    assert len(cards) >= 15
    assert len({c.id for c in cards}) == len(cards)
    monthly = [c for c in cards if c.period == "month"]
    assert monthly
    for c in monthly:
        assert c.price_azn and c.price_azn > 0
        assert c.area_m2 and c.area_m2 > 0
        assert c.url.startswith("https://bina.az/items/")


def test_search_card_fields_apartment():
    cards = parse_search_cards(load("search_xetai_menziller_2otaq.html"))
    c = next(c for c in cards if c.id == "4466741")
    assert (c.price_azn, c.period, c.rooms, c.area_m2) == (70, "day", 2, 100.0)
    assert (c.floor, c.total_floors) == (16, 16)
    assert c.location_label == "Şah İsmayıl Xətai m."
    assert c.is_vip is True
    assert card_to_listing(c, "mənzil", "Xətai") is None  # daily -> dropped


def test_card_to_listing_monthly():
    cards = parse_search_cards(load("search_xetai_menziller_2otaq.html"))
    c = next(c for c in cards if c.id == "6521964")
    x = card_to_listing(c, "mənzil", "Xətai")
    assert x.price_azn == 2300 and x.rooms == 2 and x.district == "Xətai"


def test_build_search_url():
    assert build_search_url("obyekt", "Nəsimi") == "https://bina.az/baki/nesimi/kiraye/obyektler"
    assert build_search_url("mənzil", "Xətai", 2) == "https://bina.az/baki/xetai/kiraye/menziller?room_ids%5B%5D=2"
    assert build_search_url("ofis", None, 7).endswith("/baki/kiraye/ofisler?room_ids%5B%5D=5%2B")
    with pytest.raises(ValueError):
        build_search_url("qala", "Nəsimi")


def test_normalizers():
    assert parse_int("2 500 AZN") == 2500
    assert parse_float("130,5 m²") == 130.5
    assert parse_floor("8/17 mərtəbə") == (8, 17)
    assert is_listing_url("https://bina.az/items/6603059")
    assert not is_listing_url("https://tap.az/items/1")


def test_plausibility_filter():
    cards = parse_search_cards(load("search_obyektler.html"))
    c = next(c for c in cards if c.id == "6603080")  # 350 000 AZN for 710 m² -> sale price typo
    assert not service.is_plausible(card_to_listing(c, "obyekt", "Səbail"))


def test_fetch_listing_uses_http_cache(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    db.init_db(engine)
    calls = []

    def fake_fetch(url, use_cache=True, engine=None):
        calls.append(url)
        return load("listing_ofis_6603059.html")

    monkeypatch.setattr(service, "fetch_html", fake_fetch)
    x = service.fetch_listing("https://bina.az/items/6603059", engine=engine)
    assert x.property_type == "ofis"
    again = service.fetch_listing("https://bina.az/items/6603059", engine=engine)
    assert again.price_azn == 2500
    assert len(calls) == 1  # second call served from DB
    with pytest.raises(ValueError, match="Link"):
        service.fetch_listing("https://example.com/x", engine=engine)


def test_unrenovated_listing():
    # No "Təmir" badge; description says "obyektin təmirə ehtiyacı var".
    x = parse_listing(load("listing_obyekt_temirsiz_6342627.html"), "https://bina.az/items/6342627")
    assert x.property_type == "obyekt"
    assert x.renovated is False
    assert x.area_m2 == 80.0
