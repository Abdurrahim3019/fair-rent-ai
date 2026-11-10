import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine

from src import db, tools
from src.agent import research, usage_cost
from src.models import Listing
from tests.fake_llm import ScriptedChatModel, tool_call


def L(i, price, area, district="Nəsimi", rooms=2, ptype="mənzil"):
    return Listing(id=str(i), url=f"https://bina.az/items/{i}", property_type=ptype, price_azn=price,
                   rooms=rooms, area_m2=area, district=district, scraped_at=datetime.now())


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    db.init_db(engine)
    monkeypatch.setattr(db, "_engine", engine)
    monkeypatch.setitem(tools.LIVE_SEARCH, "enabled", False)
    target = L(0, 720, 60)
    db.upsert_listings([target] + [L(i, 550 + i * 10, 55 + i) for i in range(1, 11)] + [L(50, 900, 60, ptype="ofis")])
    return target


def test_search_tool_returns_json():
    out = json.loads(tools.search_comparables_tool.invoke(
        {"property_type": "mənzil", "district": "Nəsimi", "area_min": 40, "area_max": 80}))
    assert out["count"] == 11  # includes the target itself; agent is told to skip it
    assert {"id", "price", "m2", "district"} <= set(out["listings"][0])


def test_search_tool_bad_district_is_error_not_exception():
    out = json.loads(tools.search_comparables_tool.invoke({"property_type": "mənzil", "district": "Paris"}))
    assert "error" in out


def test_stats_tool():
    out = json.loads(tools.compute_market_stats_tool.invoke({"prices": [500, 550, 600, 650, 700], "areas": [50] * 5}))
    assert out["median_price"] == 600


def test_widen_tool(tmp_db):
    out = json.loads(tools.widen_search_tool.invoke({"listing_id": "0", "reason": "az nəticə"}))
    assert "Yasamal" in out["widened_to"] and out["count"] == 10


def test_agent_selects_valid_ids_and_drops_hallucinations(tmp_db):
    ids = [str(i) for i in range(1, 11)]
    llm = ScriptedChatModel(responses=[
        tool_call("search_comparables", {"property_type": "mənzil", "district": "Nəsimi", "rooms": 2}, "c1"),
        tool_call("CandidateSelection", {
            "candidates": [{"id": i, "reason": "eyni rayon"} for i in ids]
            + [{"id": "999", "reason": "uydurma"}, {"id": "50", "reason": "ofis"}, {"id": "0", "reason": "özü"}],
            "note": "Nəsimidə kifayət qədər elan tapıldı",
        }, "c2"),
    ])
    steps = []
    res = research(tmp_db, llm=llm, on_step=steps.append)
    assert res.used_agent
    assert sorted(c.id for c in res.candidates) == sorted(ids)  # 999, ofis and self dropped
    assert all(c.reason == "eyni rayon" for c in res.candidates)
    assert any("axtarılır" in s for s in steps)
    assert res.input_tokens == 2000 and res.output_tokens == 200
    assert res.cost_usd == pytest.approx(2000 / 1e6 * 0.15 + 200 / 1e6 * 0.60)


def test_agent_failure_falls_back(tmp_db):
    llm = ScriptedChatModel(responses=[
        tool_call("CandidateSelection", {"candidates": [{"id": "999", "reason": "x"}]}, "c1"),
    ])
    res = research(tmp_db, llm=llm)
    assert not res.used_agent
    assert len(res.candidates) >= 8
    assert any("sadə axtarış" in s for s in res.steps)


def test_no_api_key_uses_deterministic(tmp_db, monkeypatch):
    monkeypatch.setattr("src.agent.llm_available", lambda: False)
    res = research(tmp_db)
    assert not res.used_agent and len(res.candidates) >= 8


def test_usage_cost_known_model():
    tin, tout, cost = usage_cost({"gpt-4o-mini-2024-07-18": {"input_tokens": 10_000, "output_tokens": 1_000}})
    assert (tin, tout) == (10_000, 1_000)
    assert cost == pytest.approx(0.0021)


def test_parallel_gather_with_cached_listing(tmp_db, monkeypatch):
    from src import chains

    monkeypatch.setattr(chains, "fetch_listing", lambda url, **kw: db.get_listing("0"))
    out = chains.parallel_gather.invoke("https://bina.az/items/0")
    assert out["listing"].id == "0"
    assert "mənzil" in out["district_snapshot"]
    assert len(out["comparables_seed"]) >= 8


def test_agent_short_list_is_topped_up(tmp_db):
    llm = ScriptedChatModel(responses=[
        tool_call("CandidateSelection", {"candidates": [{"id": i, "reason": "agent"} for i in ("1", "2", "3", "4")]}, "c1"),
    ])
    res = research(tmp_db, llm=llm)
    assert res.used_agent
    assert len(res.candidates) == 8
    assert sum(c.reason == "agent" for c in res.candidates) == 4
    assert any("tamamlandı" in s for s in res.steps)
