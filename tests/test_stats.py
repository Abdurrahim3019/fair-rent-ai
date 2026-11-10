import pytest

from src.models import AreaQuery, Listing, MarketStats
from src.stats import (
    compare_districts,
    compute_market_stats,
    compute_verdict,
    iqr_filter,
    percentile,
    percentile_rank,
)

PRICES = [500, 550, 600, 650, 700]
AREAS = [50, 55, 60, 65, 70]


def make_listing(i: int, district: str, price: int, area: float, ptype: str = "obyekt") -> Listing:
    return Listing(
        id=f"{district}-{i}",
        url=f"https://bina.az/items/{i}",
        property_type=ptype,
        price_azn=price,
        area_m2=area,
        district=district,
    )


def test_basic_stats():
    s = compute_market_stats(PRICES, AREAS, target_price=720)
    assert s.n == 5
    assert s.median_price == 600
    assert s.p25 == 550
    assert s.p75 == 650
    assert s.median_price_per_m2 == 10.0
    assert s.listing_percentile == 100.0


def test_percentile_interpolation():
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([10], 75) == 10
    assert percentile_rank([1, 2, 3, 4], 3) == 62.5


def test_iqr_filter_drops_outlier():
    assert 5000 not in iqr_filter([500, 550, 600, 650, 700, 5000])


def test_verdict_expensive():
    s = compute_market_stats(PRICES, AREAS)
    v = compute_verdict(720, s, "mənzil", user_guess=500)
    assert v.label == "baha"
    assert v.diff_azn == 120
    assert v.diff_pct == 20.0
    assert v.fair_range == (550, 650)
    assert v.user_guess == 500


@pytest.mark.parametrize(
    "price,label",
    [(600, "ədalətli"), (642, "ədalətli"), (643, "baha"), (558, "ədalətli"), (550, "ucuz")],
)
def test_verdict_thresholds_residential(price, label):
    s = compute_market_stats(PRICES, AREAS)
    assert compute_verdict(price, s, "mənzil").label == label


def test_commercial_threshold_is_wider():
    s = compute_market_stats(PRICES, AREAS)
    # +8% is "baha" for an apartment but "ədalətli" for a commercial object (±10%).
    assert compute_verdict(648, s, "mənzil").label == "baha"
    assert compute_verdict(648, s, "obyekt").label == "ədalətli"


def test_verdict_not_enough_data():
    s = MarketStats(n=4, median_price=600, p25=550, p75=650, median_price_per_m2=10)
    assert compute_verdict(720, s).label == "məlumat azdır"
    assert compute_verdict(720, None).label == "məlumat azdır"


def test_deterministic():
    s = compute_market_stats(PRICES, AREAS)
    assert compute_verdict(720, s) == compute_verdict(720, s)


def test_compare_districts_sorting_and_best():
    data = {
        # 20 AZN/m²
        "Nəsimi": [make_listing(i, "Nəsimi", 1200 + i * 10, 60) for i in range(6)],
        # ~15 AZN/m², cheapest with enough data
        "Yasamal": [make_listing(i, "Yasamal", 900 + i * 10, 60) for i in range(6)],
        # cheapest per m², but only 3 listings -> not eligible
        "Nərimanov": [make_listing(i, "Nərimanov", 500, 60) for i in range(3)],
    }
    q = AreaQuery(property_type="obyekt", districts=list(data), area_min=50, area_max=80, budget_azn=1500)
    summaries, best = compare_districts(q, data)
    assert [s.district for s in summaries] == ["Yasamal", "Nəsimi", "Nərimanov"]
    assert best == "Yasamal"
    nar = next(s for s in summaries if s.district == "Nərimanov")
    assert nar.enough_data is False
    assert summaries[0].within_budget == 6


def test_compare_districts_filters_type_and_area():
    data = {"Nəsimi": [make_listing(i, "Nəsimi", 1000, 60, "ofis") for i in range(6)]}
    q = AreaQuery(property_type="obyekt", districts=["Nəsimi"])
    summaries, best = compare_districts(q, data)
    assert summaries[0].n == 0
    assert best is None


def test_compare_districts_budget_excludes_unaffordable():
    data = {"Səbail": [make_listing(i, "Səbail", 3000, 60) for i in range(6)]}
    q = AreaQuery(property_type="obyekt", districts=["Səbail"], budget_azn=1500)
    _, best = compare_districts(q, data)
    assert best is None
