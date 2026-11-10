from datetime import datetime

import pytest
from sqlalchemy import create_engine

from src import db
from src.analysis import analyze_listing
from src.comparables import find_comparables, similarity
from src.models import Listing


def L(i, price, area, district="Nəsimi", rooms=2, ptype="mənzil", **kw):
    return Listing(
        id=str(i), url=f"https://bina.az/items/{i}", property_type=ptype, price_azn=price,
        rooms=rooms, area_m2=area, district=district, scraped_at=datetime.now(), **kw,
    )


@pytest.fixture
def engine(tmp_path):
    e = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    db.init_db(e)
    return e


def test_similarity_prefers_same_rooms_and_area():
    t = L(0, 700, 60)
    close, _ = similarity(t, L(1, 700, 62))
    far, _ = similarity(t, L(2, 700, 100, rooms=4))
    assert close > far


def test_commercial_ignores_rooms():
    t = L(0, 1500, 80, ptype="obyekt", rooms=None)
    a, _ = similarity(t, L(1, 1500, 80, ptype="obyekt", rooms=None))
    b, _ = similarity(t, L(2, 1500, 80, ptype="obyekt", rooms=3))
    assert a == b


def test_find_comparables_widens_to_neighbors(engine):
    target = L(0, 720, 60)
    db.upsert_listings(
        [L(i, 600 + i, 60) for i in range(1, 4)]  # 3 in Nəsimi
        + [L(i, 600 + i, 60, district="Yasamal") for i in range(10, 16)]  # 6 in neighbor
        + [L(99, 600, 60, ptype="ofis")],  # other type, never used
        engine=engine,
    )
    steps = []
    comps = find_comparables(target, engine=engine, steps=steps)
    assert len(comps) == 9
    assert all(c.property_type == "mənzil" for c in comps)
    assert comps[0].district == "Nəsimi"  # same district ranks higher
    assert any("qonşu" in s for s in steps)


def test_analyze_listing_end_to_end(engine):
    prices = [500, 550, 580, 600, 600, 620, 650, 700]
    db.upsert_listings([L(i, p, 60, floor=i) for i, p in enumerate(prices, 1)], engine=engine)
    res = analyze_listing(L(0, 720, 60), user_guess=550, engine=engine)
    assert res.stats.median_price == 600
    assert res.verdict.label == "baha"
    assert res.verdict.diff_azn == 120


def test_analyze_not_enough_data(engine):
    db.upsert_listings([L(1, 600, 60), L(2, 650, 60)], engine=engine)
    res = analyze_listing(L(0, 720, 60), engine=engine)
    assert res.verdict.label == "məlumat azdır"


def test_duplicates_removed(engine):
    db.upsert_listings([L(1, 900, 130), L(2, 900, 130), L(3, 950, 130)], engine=engine)
    comps = find_comparables(L(0, 900, 130), engine=engine)
    assert len(comps) == 2
