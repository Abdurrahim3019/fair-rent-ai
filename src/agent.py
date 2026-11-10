"""ReAct research agent (LangChain 1.x create_agent) that finds comparables.

The agent only *chooses* candidate ids; prices and stats always come from the
DB/tools. If no API key is set or the agent fails, the deterministic
find_comparables() is used instead, so the app keeps working offline.
"""
import json
import logging
from collections.abc import Callable

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import BaseModel, Field

from src import db
from src.comparables import MAX_COMPARABLES, dedupe, find_comparables, similarity
from src.config import (
    AGENT_RECURSION_LIMIT,
    DEFAULT_MODEL_PRICE,
    MODEL_PRICES,
    TARGET_COMPARABLES,
    llm_available,
    settings,
)
from src.models import Comparable, Listing
from src.tools import RESEARCH_TOOLS

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Sən Bakı kirayə bazarı analitikisən (yaşayış və kommersiya). Məqsədin hədəf elan üçün \
eyni əmlak növündən ən az 8, ideal halda 10–15 oxşar elan tapmaqdır. Tool-ları lazım olduğu qədər çağır. \
Rəqəmləri özün uydurma, yalnız tool nəticələrindən istifadə et.

Qaydalar:
- Həmişə hədəf elanın property_type-ı ilə axtar.
- Mənzil / həyət evi: otaq sayı, sahə (±30%), rayon, təmir vacibdir.
- Ofis / obyekt: otaq sayına baxma; sahə, rayon, mərtəbə (obyektdə 1-ci mərtəbə) və metro vacibdir.
- İlk search_comparables çağırışında sahə filtrini (area_min/area_max) vermə: bütün rayon elanlarını al, sonra özün sahəsi yaxın olanları seç.
- Lazım olsa, qonşu rayonlarda da search_comparables çağır.
- 8-dən az uyğun elan varsa widen_search çağır.
- Hədəf elanın özünü (eyni id) seçmə.
- Sonda yalnız tool nəticələrində gördüyün id-ləri qaytar, hər biri üçün qısa səbəb yaz (Azərbaycan dilində)."""


class SelectedCandidate(BaseModel):
    id: str = Field(description="Listing id from tool results")
    reason: str = Field(description="Short reason in Azerbaijani why it is comparable")


class CandidateSelection(BaseModel):
    candidates: list[SelectedCandidate] = Field(description="10–15 most comparable listings")
    note: str = Field("", description="One-sentence summary of how the search went, in Azerbaijani")


class ResearchResult(BaseModel):
    candidates: list[Comparable]
    steps: list[str]
    used_agent: bool
    note: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def get_llm(model: str | None = None) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=model or settings.openai_model, temperature=0)


def build_agent(llm: BaseChatModel | None = None):
    return create_agent(
        llm or get_llm(),
        tools=RESEARCH_TOOLS,
        system_prompt=SYSTEM_PROMPT,
        response_format=ToolStrategy(CandidateSelection),
        name="research_agent",
    )


def usage_cost(usage: dict) -> tuple[int, int, float]:
    """Sum tokens and USD cost from UsageMetadataCallbackHandler.usage_metadata."""
    tin = tout = 0
    cost = 0.0
    for model_name, u in usage.items():
        i, o = u.get("input_tokens", 0), u.get("output_tokens", 0)
        price = next((p for k, p in MODEL_PRICES.items() if model_name.startswith(k)), DEFAULT_MODEL_PRICE)
        cost += i / 1e6 * price[0] + o / 1e6 * price[1]
        tin, tout = tin + i, tout + o
    return tin, tout, round(cost, 6)


def _describe(msg) -> str | None:
    """Human-friendly step text for the UI status box."""
    if isinstance(msg, AIMessage) and msg.tool_calls:
        parts = []
        for tc in msg.tool_calls:
            a = tc["args"]
            if tc["name"] == "search_comparables":
                parts.append(f"Oxşar elanlar axtarılır: {a.get('district')}, {a.get('property_type')}")
            elif tc["name"] == "widen_search":
                parts.append("Axtarış genişləndirilir (qonşu rayonlar)")
            elif tc["name"] == "compute_market_stats":
                parts.append("Bazar statistikası hesablanır")
            elif tc["name"] == "CandidateSelection":
                parts.append("Ən uyğun elanlar seçilir")
            else:
                parts.append(f"{tc['name']} çağırılır")
        return " · ".join(parts)
    if isinstance(msg, ToolMessage) and msg.name != "CandidateSelection":
        try:
            data = json.loads(msg.content)
            if "error" in data:
                return f"⚠️ {data['error']}"
            if "count" in data:
                return f"→ {data['count']} elan tapıldı"
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def _user_message(target: Listing, seed: list[Comparable], snapshot: dict | None) -> str:
    payload = {
        "target": target.model_dump(exclude={"scraped_at", "url", "title"}),
        "cached_candidates": [
            {"id": c.id, "price": c.price_azn, "m2": c.area_m2, "district": c.district, "rooms": c.rooms}
            for c in seed[:10]
        ],
        "district_snapshot": snapshot,
    }
    return (
        "Hədəf elan və keşdən ilkin namizədlər aşağıdadır. Oxşar elanları tap və seç.\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def _to_comparables(target: Listing, selection: CandidateSelection) -> list[Comparable]:
    out: list[Comparable] = []
    listings = []
    reasons = {}
    for c in selection.candidates:
        if c.id == target.id or c.id in reasons:
            continue
        x = db.get_listing(c.id)
        if x is None or x.property_type != target.property_type:
            continue  # hallucinated id or wrong type -> drop
        listings.append(x)
        reasons[c.id] = c.reason
    for x in dedupe(listings):
        score, auto_reason = similarity(target, x)
        out.append(Comparable(**x.model_dump(), similarity_score=score, reason=reasons.get(x.id) or auto_reason))
    out.sort(key=lambda c: c.similarity_score, reverse=True)
    return out[:MAX_COMPARABLES]


def top_up(target: Listing, chosen: list[Comparable]) -> list[Comparable]:
    """Fill up to TARGET_COMPARABLES with the best deterministic matches not yet chosen."""
    have = {c.id for c in chosen}
    keys = {(c.price_azn, c.area_m2, c.rooms, c.district, c.floor) for c in chosen}
    extra = [
        c for c in find_comparables(target)
        if c.id not in have and (c.price_azn, c.area_m2, c.rooms, c.district, c.floor) not in keys
    ]
    merged = chosen + extra[: max(0, TARGET_COMPARABLES - len(chosen))]
    merged.sort(key=lambda c: c.similarity_score, reverse=True)
    return merged


def research(
    target: Listing,
    seed: list[Comparable] | None = None,
    snapshot: dict | None = None,
    llm: BaseChatModel | None = None,
    on_step: Callable[[str], None] | None = None,
    thread_id: str | None = None,
) -> ResearchResult:
    """Run the ReAct agent; fall back to deterministic search when unavailable/failing."""
    steps: list[str] = []

    def emit(s: str) -> None:
        steps.append(s)
        if on_step:
            on_step(s)

    seed = seed or []
    if llm is None and not llm_available():
        emit("LLM açarı yoxdur — sadə (agentsiz) axtarış istifadə olunur")
        return ResearchResult(candidates=find_comparables(target, steps=steps), steps=steps, used_agent=False)

    usage_cb = UsageMetadataCallbackHandler()
    config = {
        "recursion_limit": AGENT_RECURSION_LIMIT * 2 + 1,  # each tool round = model + tools step
        "callbacks": [usage_cb],
        "run_name": "research_agent",
        "tags": ["edaletli-kiraye", target.property_type],
        "metadata": {
            "listing_id": target.id,
            "district": target.district,
            "property_type": target.property_type,
            "thread_id": thread_id,
        },
    }
    try:
        agent = build_agent(llm)
        final_state = None
        for chunk in agent.stream(
            {"messages": [{"role": "user", "content": _user_message(target, seed, snapshot)}]},
            config=config,
            stream_mode="values",
        ):
            final_state = chunk
            msg = chunk["messages"][-1] if chunk.get("messages") else None
            text = _describe(msg) if msg is not None else None
            if text and (not steps or steps[-1] != text):
                emit(text)
        selection = final_state.get("structured_response") if final_state else None
        if selection is None:
            raise RuntimeError("Agent struktur cavab qaytarmadı")
        candidates = _to_comparables(target, selection)
        if len(candidates) < 3:
            raise RuntimeError(f"Agent az namizəd qaytardı ({len(candidates)})")
        if len(candidates) < TARGET_COMPARABLES:
            emit(f"Agent {len(candidates)} oxşar elan seçdi (hədəf: {TARGET_COMPARABLES})")
            candidates = top_up(target, candidates)
            emit(f"Siyahı ən oxşar keş elanları ilə {len(candidates)}-ə tamamlandı")
        tin, tout, cost = usage_cost(usage_cb.usage_metadata)
        return ResearchResult(
            candidates=candidates, steps=steps, used_agent=True, note=selection.note,
            input_tokens=tin, output_tokens=tout, cost_usd=cost,
        )
    except Exception as exc:
        log.warning("Agent failed, falling back: %s", exc)
        emit(f"Agent xətası ({type(exc).__name__}) — sadə axtarışa keçildi")
        tin, tout, cost = usage_cost(usage_cb.usage_metadata)
        return ResearchResult(
            candidates=find_comparables(target, steps=steps), steps=steps, used_agent=False,
            input_tokens=tin, output_tokens=tout, cost_usd=cost,
        )
