from datetime import datetime

import pytest
from sqlalchemy import create_engine

from src import chains, db, tools
from src.graph import build_graph, resume, start
from src.models import Listing
from tests.fake_llm import ScriptedChatModel, tool_call


def L(i, price, area=60, district="Nəsimi", rooms=2):
    return Listing(id=str(i), url=f"https://bina.az/items/{i}", property_type="mənzil", price_azn=price,
                   rooms=rooms, area_m2=area, district=district, floor=i, renovated=True, scraped_at=datetime.now())


PRICES = [500, 550, 580, 600, 610, 620, 650, 700, 400, 420]  # last two: cheap unrenovated comps (survive IQR)
IDS = [str(i) for i in range(1, 11)]


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    db.init_db(engine)
    monkeypatch.setattr(db, "_engine", engine)
    monkeypatch.setitem(tools.LIVE_SEARCH, "enabled", False)
    target = L(0, 640)
    db.upsert_listings([target] + [L(i, p) for i, p in zip(range(1, 11), PRICES)])
    # scraper mocked: the URL resolves to the cached target, no network
    monkeypatch.setattr(chains, "fetch_listing", lambda url, **kw: db.get_listing("0"))
    return target


def scripted_llm():
    return ScriptedChatModel(responses=[
        tool_call("search_comparables", {"property_type": "mənzil", "district": "Nəsimi"}, "c1"),
        tool_call("CandidateSelection", {"candidates": [{"id": i, "reason": "oxşar"} for i in IDS]}, "c2"),
    ])


def run_to_interrupt(thread="t1", llm=None):
    graph = build_graph(llm=llm or scripted_llm())
    steps = []
    payload = start(graph, thread, url="https://bina.az/items/0", user_guess=600, on_step=steps.append)
    return graph, payload, steps


def test_graph_stops_at_interrupt():
    graph, payload, steps = run_to_interrupt()
    assert {c["id"] for c in payload["candidates"]} == set(IDS)
    assert payload["listing"]["id"] == "0"
    assert "təmirsiz" in payload["remove_reasons"]
    assert graph.get_state({"configurable": {"thread_id": "t1"}}).next == ("human_review",)
    assert any("paralel" in s for s in steps)


def test_resume_returns_verdict_and_removal_changes_it():
    graph, _, _ = run_to_interrupt("all")
    all_state = resume(graph, "all", IDS)
    v_all = all_state["verdict"]

    graph2, _, _ = run_to_interrupt("filtered")
    filtered = resume(graph2, "filtered", IDS[:8], {"9": "təmirsiz", "10": "təmirsiz"})
    v_f = filtered["verdict"]

    # all 10: median 590 -> 640 is +8.5% -> "baha"; without the 2 unrenovated: median 605 -> +5.8% -> "ədalətli"
    assert all_state["stats"]["n"] == 10 and all_state["stats"]["median_price"] == 590
    assert v_all["label"] == "baha"
    assert v_f["label"] == "ədalətli"
    assert filtered["stats"]["n"] == 8 and filtered["stats"]["median_price"] == 605
    assert filtered["removed_reasons"] == {"9": "təmirsiz", "10": "təmirsiz"}
    assert v_f["negotiation_message"] and v_f["explanation"]
    assert v_f["user_guess"] == 600


def test_less_than_five_approved_is_not_enough_data():
    graph, _, _ = run_to_interrupt("few")
    state = resume(graph, "few", IDS[:4])
    assert state["verdict"]["label"] == "məlumat azdır"


def test_sample_listing_path_skips_fetch(tmp_db, monkeypatch):
    monkeypatch.setattr(chains, "fetch_listing", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no fetch")))
    graph = build_graph(llm=scripted_llm())
    payload = start(graph, "sample", sample=tmp_db)
    assert len(payload["candidates"]) == 10


def test_template_message_when_no_llm(monkeypatch):
    from src.chains import template_message
    from src.models import MarketStats, Verdict

    s = MarketStats(n=8, median_price=605, p25=575, p75=640, median_price_per_m2=10)
    v = Verdict(label="baha", diff_azn=115, diff_pct=19.0, fair_range=(575, 640))
    m = template_message(L(0, 720), v, s)
    assert "600 AZN" in m.negotiation_message  # counter-offer = median rounded to 50
    assert "+115 AZN" in m.explanation


def test_counter_offer_never_above_price_and_only_when_expensive():
    from src.chains import counter_offer
    from src.models import MarketStats, Verdict

    s = MarketStats(n=6, median_price=875, p25=850, p75=900, median_price_per_m2=7.7)
    fair = Verdict(label="ədalətli", diff_azn=-25, diff_pct=-2.9, fair_range=(850, 900))
    assert counter_offer(fair, s, 850) is None  # was 900 > 850 before the fix
    expensive = Verdict(label="baha", diff_azn=325, diff_pct=37, fair_range=(850, 900))
    assert counter_offer(expensive, s, 1200) == 850
    assert counter_offer(expensive, MarketStats(n=6, median_price=1000, p25=1, p75=2, median_price_per_m2=1), 980) == 980
