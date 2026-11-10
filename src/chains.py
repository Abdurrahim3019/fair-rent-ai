"""LCEL chains: parallel initial gathering before the agent runs."""
import time

from langchain_core.runnables import RunnableLambda, RunnableParallel

from src import db
from src.comparables import find_comparables
from src.config import PROPERTY_TYPES
from src.models import Listing
from src.scraper.normalize import item_id_from_url
from src.scraper.service import fetch_listing
from src.stats import summarize_district


_FETCH_OPTS = {"use_cache": True}


def _fetch(url: str) -> Listing:
    return fetch_listing(url, use_cache=_FETCH_OPTS["use_cache"])


def _district_snapshot(url: str) -> dict:
    """Per-type, per-district medians from the DB (no network). Independent of the target."""
    out: dict[str, dict] = {}
    for ptype in PROPERTY_TYPES:
        rows = {}
        for d in db.list_districts(ptype):
            s = summarize_district(d, db.query_listings(property_type=ptype, districts=[d]))
            if s.n:
                rows[d] = {"n": s.n, "median": s.median_price, "per_m2": s.median_price_per_m2}
        out[ptype] = rows
    return out


def _quick_comparables(url: str) -> list:
    """If the target is already cached in the DB, pre-compute comparables from cache."""
    item_id = item_id_from_url(url)
    cached = db.get_listing(item_id) if item_id else None
    return find_comparables(cached) if cached else []


fetch_listing_runnable = RunnableLambda(_fetch, name="fetch_listing")
district_stats_runnable = RunnableLambda(_district_snapshot, name="district_snapshot")
quick_comparables_runnable = RunnableLambda(_quick_comparables, name="quick_comparables")

parallel_gather = RunnableParallel(
    listing=fetch_listing_runnable,
    district_snapshot=district_stats_runnable,
    comparables_seed=quick_comparables_runnable,
).with_config(run_name="parallel_gather")


def gather(url: str, listing: Listing | None = None) -> dict:
    """Run the parallel step. If `listing` is given (sample button), skip the fetch."""
    if listing is not None:
        return {
            "listing": listing,
            "district_snapshot": _district_snapshot(listing.url),
            "comparables_seed": find_comparables(listing),
        }
    return parallel_gather.invoke(url)


def snapshot_for(snapshot: dict, listing: Listing) -> dict:
    """Trim the snapshot to the target type to keep the prompt small."""
    return {listing.property_type: snapshot.get(listing.property_type, {})}


def benchmark(url: str) -> tuple[float, float]:
    """(sequential_sec, parallel_sec) for README; network fetch forced (no cache) in both."""
    _FETCH_OPTS["use_cache"] = False
    t = time.perf_counter()
    _fetch(url)
    _district_snapshot(url)
    _quick_comparables(url)
    seq = time.perf_counter() - t
    t = time.perf_counter()
    parallel_gather.invoke(url)
    par = time.perf_counter() - t
    _FETCH_OPTS["use_cache"] = True
    return seq, par


# --- explanation + negotiation message (LLM writes text only, numbers come from Verdict) ---

from langchain_core.prompts import ChatPromptTemplate  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from src.models import MarketStats, Verdict  # noqa: E402


class MessageOut(BaseModel):
    explanation: str = Field(description="2–3 sentences in Azerbaijani explaining the verdict with real numbers")
    negotiation_message: str = Field(
        description="Polite message to the landlord in Azerbaijani, cites market numbers, proposes a counter-offer"
    )


MESSAGE_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "Sən Bakı kirayə bazarı üzrə köməkçisən. Yalnız verilən rəqəmlərdən istifadə et, yeni rəqəm uydurma. "
     "Azərbaycan dilində, sadə və nəzakətli yaz. "
     "explanation: hökmü 2–3 cümlə ilə izah et (median, ədalətli aralıq, fərq). "
     "Hökm 'ucuz'dursa, 'şübhəli dərəcədə ucuzdursa, elanı diqqətlə yoxla' xəbərdarlığını əlavə et. "
     "negotiation_message: ev sahibinə 4–6 cümləlik mesaj. Salamla başla, elana maraq bildir, "
     "oxşar elanların sayına və median qiymətə istinad et, counter_offer məbləğini təklif et. "
     "Hökm 'ədalətli' və ya 'ucuz'dursa, heç bir məbləğ təklif etmə və qiyməti endirməyə/artırmağa çalışma — "
     "yalnız maraq bildir və baxış vaxtı soruş. Heç vaxt elandakı qiymətdən yüksək məbləğ yazma."),
    ("human",
     "Elan: {property_type}, {district}, {area} m², {rooms_text}qiymət {price} AZN/ay.\n"
     "Hökm: {label}. Bazar medianı: {median} AZN, ədalətli aralıq: {low}–{high} AZN, "
     "fərq: {diff_azn} AZN ({diff_pct}%), müqayisə edilən elan sayı: {n}, 1 m² medianı: {per_m2} AZN.\n"
     "Təklif ediləcək məbləğ (counter_offer): {counter_offer} AZN."),
])


def counter_offer(verdict: Verdict, stats: MarketStats | None, listing_price: int | None = None) -> int | None:
    """Counter-offer only for "baha": market median rounded down to 50, never above the listing price."""
    if stats is None or verdict.label != "baha":
        return None
    offer = int(stats.median_price // 50 * 50)
    if listing_price is not None:
        offer = min(offer, listing_price)
    return offer


def template_message(listing: Listing, verdict: Verdict, stats: MarketStats | None) -> MessageOut:
    """No-LLM fallback with the same numbers."""
    if stats is None or verdict.label == "məlumat azdır":
        return MessageOut(
            explanation="Bu elan üçün kifayət qədər oxşar elan tapılmadı, ona görə etibarlı hökm vermək mümkün deyil.",
            negotiation_message=(
                f"Salam! {listing.district} rayonundakı {listing.area_m2:g} m² elanınızla maraqlanıram. "
                "Qiymət barədə danışmaq və yeri görmək mümkündürmü?"
            ),
        )
    lo, hi = verdict.fair_range
    expl = (
        f"{stats.n} oxşar elanın medianı {stats.median_price:.0f} AZN, ədalətli aralıq {lo}–{hi} AZN-dir. "
        f"Bu elan bazardan {verdict.diff_azn:+d} AZN ({verdict.diff_pct:+.0f}%) fərqlənir."
    )
    if verdict.label == "ucuz":
        expl += " Qiymət şübhəli dərəcədə ucuzdursa, elanı diqqətlə yoxla."
    if verdict.label == "baha":
        offer = counter_offer(verdict, stats, listing.price_azn)
        msg = (
            f"Salam! {listing.district} rayonundakı {listing.area_m2:g} m² elanınızla maraqlanıram. "
            f"Bu ərazidə oxşar {stats.n} elanın orta qiyməti təxminən {stats.median_price:.0f} AZN-dir. "
            f"Aylıq {offer} AZN-ə razılaşa bilərikmi? Uzunmüddətli kirayəçi olmağa hazıram."
        )
    else:
        msg = (
            f"Salam! {listing.district} rayonundakı {listing.area_m2:g} m² elanınızla maraqlanıram. "
            "Qiymət mənə uyğundur. Yeri nə vaxt görmək mümkündür?"
        )
    return MessageOut(explanation=expl, negotiation_message=msg)


def write_message(listing: Listing, verdict: Verdict, stats: MarketStats | None, llm=None, config=None) -> MessageOut:
    from src.config import llm_available

    if stats is None or verdict.label == "məlumat azdır" or (llm is None and not llm_available()):
        return template_message(listing, verdict, stats)
    if llm is None:
        from src.agent import get_llm

        llm = get_llm()
    try:
        chain = (MESSAGE_PROMPT | llm.with_structured_output(MessageOut)).with_config(run_name="write_message")
        return chain.invoke(
            {
                "property_type": listing.property_type, "district": listing.district, "area": f"{listing.area_m2:g}",
                "rooms_text": f"{listing.rooms} otaq, " if listing.rooms else "", "price": listing.price_azn,
                "label": verdict.label, "median": f"{stats.median_price:.0f}", "low": verdict.fair_range[0],
                "high": verdict.fair_range[1], "diff_azn": verdict.diff_azn, "diff_pct": verdict.diff_pct,
                "n": stats.n, "per_m2": stats.median_price_per_m2,
                "counter_offer": counter_offer(verdict, stats, listing.price_azn) or "yoxdur (qiymət endirmə)",
            },
            config=config,
        )
    except Exception:
        return template_message(listing, verdict, stats)
