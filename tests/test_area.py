from datetime import datetime

import pytest
from sqlalchemy import create_engine

from src import area, db
from src.charts import district_bar_chart
from src.models import AreaQuery, Listing


def L(i, district, price, area_m2, ptype="obyekt", floor=None):
    return Listing(id=f"{district}-{i}", url=f"https://bina.az/items/{abs(hash((district, i))) % 10**7}",
                   property_type=ptype, price_azn=price, area_m2=area_m2, district=district,
                   floor=floor if floor is not None else i, scraped_at=datetime.now())


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    db.init_db(engine)
    monkeypatch.setattr(db, "_engine", engine)
    monkeypatch.setattr(area, "llm_available", lambda: False)
    db.upsert_listings(
        [L(i, "Nəsimi", 1600 + i * 50, 60) for i in range(6)]         # ~27 AZN/m²
        + [L(i, "Yasamal", 1000 + i * 40, 60) for i in range(6)]      # ~17 AZN/m²
        + [L(i, "Nərimanov", 700, 60) for i in range(3)]               # cheap but only 3
        + [L(i, "Yasamal", 900, 200) for i in range(10, 13)]           # outside area range
        + [L(i, "Nəsimi", 500, 60, ptype="ofis") for i in range(20, 26)]  # other type
    )


def test_area_report_end_to_end():
    q = AreaQuery(property_type="obyekt", districts=["Nəsimi", "Yasamal", "Nərimanov"],
                  area_min=50, area_max=80, budget_azn=1500)
    steps = []
    res = area.build_area_report(q, on_step=steps.append)
    rep = res.report
    assert rep.best_value_district == "Yasamal"
    assert [s.district for s in rep.districts] == ["Yasamal", "Nəsimi", "Nərimanov"]
    yas = rep.districts[0]
    assert yas.n == 6 and yas.within_budget == 6
    assert rep.districts[2].enough_data is False
    assert all(x.price_azn <= 1500 for x in rep.matching_listings)
    assert rep.matching_listings[0].district == "Yasamal"  # best district first
    assert "Yasamal" in rep.summary and "Nərimanov" in rep.summary  # template mentions low-data district
    assert not res.used_llm and res.cost_usd == 0
    assert any("paralel" in s for s in steps)


def test_gather_districts_filters_type_and_area():
    q = AreaQuery(property_type="obyekt", districts=["Yasamal", "Nəsimi"], area_min=50, area_max=80)
    by = area.gather_districts(q)
    assert len(by["Yasamal"]) == 6  # the 200 m² ones are filtered out
    assert all(x.property_type == "obyekt" for xs in by.values() for x in xs)


def test_no_data_message():
    q = AreaQuery(property_type="obyekt", districts=["Xəzər"], area_min=50, area_max=80)
    rep = area.build_area_report(q).report
    assert rep.best_value_district is None
    assert "kifayət qədər elan yoxdur" in rep.summary


def test_district_bar_chart_spec():
    q = AreaQuery(property_type="obyekt", districts=["Nəsimi", "Yasamal", "Nərimanov"], budget_azn=1500)
    rep = area.build_area_report(q).report
    spec = district_bar_chart(rep.districts, 1500, rep.best_value_district).to_dict()
    text = str(spec)
    assert "büdcə 1500" in text and "Yasamal 🏆" in text and "məlumat azdır" in text


def test_summary_guard_rejects_false_low_data_claim():
    from src.models import DistrictSummary

    ok = [DistrictSummary(district="Nərimanov", n=6, enough_data=True), DistrictSummary(district="Yasamal", n=9, enough_data=True)]
    assert not area.summary_is_consistent("Nərimanov ucuzdur. Yasamalda məlumat azdır.", ok, "Nərimanov")
    assert not area.summary_is_consistent("Yasamal ən sərfəlidir.", ok, "Nərimanov")
    assert area.summary_is_consistent("Ən sərfəli bölgə Nərimanovdur.", ok, "Nərimanov")
