"""LangGraph workflow with human-in-the-loop review of comparables.

START → parallel_gather → research_agent → human_review (interrupt) → compute_verdict → write_message → END

State values are stored as JSON-able dicts so the MemorySaver checkpoint is safe.
"""
from typing import Any, TypedDict

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from src.agent import research, usage_cost
from src.analysis import stats_for
from src.chains import gather, snapshot_for, write_message
from src.config import MIN_COMPARABLES
from src.models import Comparable, Listing, MarketStats, Verdict
from src.stats import compute_verdict

REMOVE_REASONS = ["təmirsiz", "fərqli rayon", "çox böyük/kiçik", "digər"]


class State(TypedDict, total=False):
    # input
    url: str
    sample_listing: dict | None
    user_guess: int | None
    thread_id: str
    # gathered
    listing: dict
    district_snapshot: dict
    seed: list[dict]
    # research
    candidates: list[dict]
    steps: list[str]
    used_agent: bool
    # human review
    approved: list[dict]
    removed_reasons: dict[str, str]
    # result
    stats: dict | None
    verdict: dict
    input_tokens: int
    output_tokens: int
    cost_usd: float


def _say(text: str) -> None:
    try:
        get_stream_writer()({"step": text})
    except Exception:  # called outside a graph run (tests)
        pass


def parallel_gather_node(state: State) -> dict:
    _say("Elan oxunur, rayon statistikası və keş paralel yüklənir…")
    sample = Listing.model_validate(state["sample_listing"]) if state.get("sample_listing") else None
    g = gather(state.get("url", ""), listing=sample)
    target: Listing = g["listing"]
    _say(f"Elan: {target.property_type}, {target.district}, {target.area_m2:g} m², {target.price_azn} AZN")
    return {
        "listing": target.model_dump(mode="json"),
        "district_snapshot": snapshot_for(g["district_snapshot"], target),
        "seed": [c.model_dump(mode="json") for c in g["comparables_seed"]],
    }


def make_research_node(llm=None):
    def research_node(state: State) -> dict:
        target = Listing.model_validate(state["listing"])
        seed = [Comparable.model_validate(c) for c in state.get("seed", [])]
        r = research(target, seed=seed, snapshot=state.get("district_snapshot"), llm=llm,
                     on_step=_say, thread_id=state.get("thread_id"))
        return {
            "candidates": [c.model_dump(mode="json") for c in r.candidates],
            "steps": r.steps,
            "used_agent": r.used_agent,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "cost_usd": r.cost_usd,
        }

    return research_node


def human_review_node(state: State) -> dict:
    """Pause and let the user drop unsuitable comparables."""
    decision: dict[str, Any] = interrupt({
        "listing": state["listing"],
        "candidates": state["candidates"],
        "remove_reasons": REMOVE_REASONS,
    })
    approved_ids = set(decision.get("approved_ids", []))
    approved = [c for c in state["candidates"] if c["id"] in approved_ids]
    return {"approved": approved, "removed_reasons": decision.get("removed_reasons", {})}


def compute_verdict_node(state: State) -> dict:
    """Pure Python, no LLM."""
    target = Listing.model_validate(state["listing"])
    approved = [Comparable.model_validate(c) for c in state.get("approved", [])]
    stats = stats_for(target, approved) if approved else None
    # compute_verdict returns "məlumat azdır" when stats.n < MIN_COMPARABLES.
    verdict = compute_verdict(target.price_azn, stats, target.property_type, state.get("user_guess"))
    _say(f"{len(approved)} təsdiqlənmiş elanla hökm hesablandı" + ("" if len(approved) >= MIN_COMPARABLES else " (az)"))
    return {
        "stats": stats.model_dump(mode="json") if stats else None,
        "verdict": verdict.model_dump(mode="json"),
    }


def make_write_message_node(llm=None):
    def write_message_node(state: State) -> dict:
        _say("İzah və danışıq mesajı yazılır…")
        target = Listing.model_validate(state["listing"])
        verdict = Verdict.model_validate(state["verdict"])
        stats = MarketStats.model_validate(state["stats"]) if state.get("stats") else None
        cb = UsageMetadataCallbackHandler()
        out = write_message(target, verdict, stats, llm=llm, config={"callbacks": [cb]})
        verdict.explanation = out.explanation
        verdict.negotiation_message = out.negotiation_message
        tin, tout, cost = usage_cost(cb.usage_metadata)
        return {
            "verdict": verdict.model_dump(mode="json"),
            "input_tokens": state.get("input_tokens", 0) + tin,
            "output_tokens": state.get("output_tokens", 0) + tout,
            "cost_usd": round(state.get("cost_usd", 0.0) + cost, 6),
        }

    return write_message_node


def build_graph(llm=None, checkpointer=None):
    g = StateGraph(State)
    g.add_node("parallel_gather", parallel_gather_node)
    g.add_node("research_agent", make_research_node(llm))
    g.add_node("human_review", human_review_node)
    g.add_node("compute_verdict", compute_verdict_node)
    g.add_node("write_message", make_write_message_node(llm))
    g.add_edge(START, "parallel_gather")
    g.add_edge("parallel_gather", "research_agent")
    g.add_edge("research_agent", "human_review")
    g.add_edge("human_review", "compute_verdict")
    g.add_edge("compute_verdict", "write_message")
    g.add_edge("write_message", END)
    return g.compile(checkpointer=checkpointer or MemorySaver(), name="edaletli_kiraye")


def run_config(thread_id: str, listing_id: str | None = None) -> dict:
    return {
        "configurable": {"thread_id": thread_id},
        "run_name": "edaletli_kiraye_graph",
        "tags": ["edaletli-kiraye"],
        "metadata": {"thread_id": thread_id, **({"listing_id": listing_id} if listing_id else {})},
    }


def start(graph, thread_id: str, url: str | None = None, sample: Listing | None = None,
          user_guess: int | None = None, on_step=None) -> dict:
    """Run until the human_review interrupt. Returns the interrupt payload."""
    inputs: State = {
        "url": url or "", "sample_listing": sample.model_dump(mode="json") if sample else None,
        "user_guess": user_guess, "thread_id": thread_id,
    }
    cfg = run_config(thread_id, sample.id if sample else None)
    for mode, chunk in graph.stream(inputs, cfg, stream_mode=["custom", "updates"]):
        if mode == "custom" and on_step and isinstance(chunk, dict) and "step" in chunk:
            on_step(chunk["step"])
    snap = graph.get_state(cfg)
    if not snap.interrupts:
        raise RuntimeError("Qraf gözlənilən yoxlama addımında dayanmadı")
    return snap.interrupts[0].value


def resume(graph, thread_id: str, approved_ids: list[str], removed_reasons: dict[str, str] | None = None,
           on_step=None) -> State:
    cfg = run_config(thread_id)
    cfg["metadata"]["n_approved"] = len(approved_ids)
    cfg["metadata"]["n_removed"] = len(removed_reasons or {})
    if removed_reasons:
        cfg["metadata"]["removed_reasons"] = sorted(set(removed_reasons.values()))
    cmd = Command(resume={"approved_ids": approved_ids, "removed_reasons": removed_reasons or {}})
    for mode, chunk in graph.stream(cmd, cfg, stream_mode=["custom", "updates"]):
        if mode == "custom" and on_step and isinstance(chunk, dict) and "step" in chunk:
            on_step(chunk["step"])
    return graph.get_state(cfg).values
