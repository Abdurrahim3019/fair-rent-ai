"""Analysis pipeline: parallel gather -> research agent -> stats -> verdict."""
import random
from collections.abc import Callable

from langsmith import get_current_run_tree, traceable
from pydantic import BaseModel

from src import db
from src.comparables import find_comparables
from src.models import Comparable, Listing, MarketStats, Verdict
from src.stats import compute_market_stats, compute_verdict, iqr_filter


class AnalysisResult(BaseModel):
    listing: Listing
    comparables: list[Comparable]
    stats: MarketStats | None
    verdict: Verdict
    steps: list[str]
    used_agent: bool = False
    note: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def stats_for(listing: Listing, comparables: list[Listing]) -> MarketStats | None:
    if not comparables:
        return None
    kept = comparables
    if len(comparables) >= 8:  # IQR on tiny samples throws away too much
        kept_prices = set(iqr_filter([float(c.price_azn) for c in comparables]))
        kept = [c for c in comparables if float(c.price_azn) in kept_prices]
    return compute_market_stats(
        [c.price_azn for c in kept], [c.area_m2 for c in kept], target_price=listing.price_azn
    )


def analyze_listing(listing: Listing, user_guess: int | None = None, live: bool = False, engine=None) -> AnalysisResult:
    """Deterministic pipeline (no LLM)."""
    steps: list[str] = [f"Elan oxundu: {listing.property_type}, {listing.district}, {listing.area_m2:g} m²"]
    comps = find_comparables(listing, live=live, engine=engine, steps=steps)
    stats = stats_for(listing, comps)
    verdict = compute_verdict(listing.price_azn, stats, listing.property_type, user_guess)
    return AnalysisResult(listing=listing, comparables=comps, stats=stats, verdict=verdict, steps=steps)


@traceable(name="edaletli_kiraye_analysis", tags=["edaletli-kiraye"])
def analyze(
    url: str | None = None,
    listing: Listing | None = None,
    user_guess: int | None = None,
    on_step: Callable[[str], None] | None = None,
    thread_id: str | None = None,
    llm=None,
) -> AnalysisResult:
    """Full pipeline with the ReAct agent (falls back to deterministic search)."""
    from src.agent import research
    from src.chains import gather, snapshot_for

    say = on_step or (lambda _s: None)
    say("Elan oxunur, rayon statistikası və keş paralel yüklənir…")
    g = gather(url or "", listing=listing)
    target: Listing = g["listing"]
    say(f"Elan: {target.property_type}, {target.district}, {target.area_m2:g} m², {target.price_azn} AZN")

    r = research(
        target, seed=g["comparables_seed"], snapshot=snapshot_for(g["district_snapshot"], target),
        llm=llm, on_step=say, thread_id=thread_id,
    )
    stats = stats_for(target, r.candidates)
    verdict = compute_verdict(target.price_azn, stats, target.property_type, user_guess)
    run = get_current_run_tree()
    if run is not None:
        run.add_metadata({
            "listing_id": target.id, "district": target.district, "property_type": target.property_type,
            "n_comparables": stats.n if stats else 0, "verdict": verdict.label, "used_agent": r.used_agent,
            "thread_id": thread_id, "cost_usd": r.cost_usd,
        })
    return AnalysisResult(
        listing=target, comparables=r.candidates, stats=stats, verdict=verdict, steps=r.steps,
        used_agent=r.used_agent, note=r.note, input_tokens=r.input_tokens,
        output_tokens=r.output_tokens, cost_usd=r.cost_usd,
    )


def sample_listing(engine=None) -> Listing | None:
    """A DB listing that has enough comparables, for the offline demo button."""
    pool = [x for x in db.query_listings(property_type="mənzil", engine=engine) if x.renovated]
    random.shuffle(pool)
    for x in pool[:30]:
        if len(find_comparables(x, engine=engine)) >= 8:
            return x
    return pool[0] if pool else None
