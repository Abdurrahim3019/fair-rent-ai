"""'Bölgə üzrə bax' mode: compare districts for a property type, area range and budget.

START → compare_districts (RunnableParallel, one branch per district) → write_area_summary → END
Statistics are pure Python; the LLM only writes a 2–3 sentence summary.
"""
from collections.abc import Callable

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda, RunnableParallel
from langsmith import get_current_run_tree, traceable
from pydantic import BaseModel, Field

from src import db
from src.comparables import dedupe
from src.config import MAX_MATCHING_LISTINGS, llm_available
from src.models import AreaQuery, AreaReport, DistrictSummary, Listing
from src.stats import compare_districts, filter_for_query


class AreaResult(BaseModel):
    report: AreaReport
    listings_by_district: dict[str, list[Listing]]
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    used_llm: bool = False


def refresh_live(q: AreaQuery, say: Callable[[str], None]) -> None:
    """Sequential live scrape with ONE browser (polite: never parallel requests to bina.az)."""
    from src.scraper.selenium_fallback import chrome_driver
    from src.scraper.service import search_live

    with chrome_driver() as driver:
        for d in q.districts:
            try:
                fresh = search_live(q.property_type, d, rooms=q.rooms, pages=2, driver=driver)
                db.upsert_listings([x for x in fresh if db.get_listing(x.id) is None])
                say(f"{d}: bina.az-dan {len(fresh)} elan yoxlanıldı")
            except Exception as exc:
                say(f"{d}: canlı yeniləmə alınmadı ({type(exc).__name__}), keş istifadə olunur")


def _district_branch(district: str) -> RunnableLambda:
    def load(q: AreaQuery) -> list[Listing]:
        rows = db.query_listings(property_type=q.property_type, districts=[district])
        return dedupe(filter_for_query(rows, q))

    return RunnableLambda(load, name=f"district_{district}")


def gather_districts(q: AreaQuery) -> dict[str, list[Listing]]:
    """Load all selected districts in parallel (one RunnableParallel branch each)."""
    par = RunnableParallel({d: _district_branch(d) for d in q.districts}).with_config(
        run_name="compare_districts_parallel"
    )
    return par.invoke(q)


def matching_listings(q: AreaQuery, by_district: dict[str, list[Listing]], best: str | None) -> list[Listing]:
    """Within budget, best-value district first, then cheapest per m²."""
    pool = [x for xs in by_district.values() for x in xs]
    if q.budget_azn is not None:
        pool = [x for x in pool if x.price_azn <= q.budget_azn]
    pool.sort(key=lambda x: (x.district != best, x.price_per_m2))
    return pool[:MAX_MATCHING_LISTINGS]


class AreaSummaryOut(BaseModel):
    summary: str = Field(description="2–3 sentences in Azerbaijani, only using the given numbers")


AREA_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "Sən Bakı kirayə bazarı üzrə məsləhətçisən. Yalnız verilən rəqəmlərdən istifadə et, rəqəm uydurma. "
     "Azərbaycan dilində 2–3 cümlə yaz: ən sərfəli bölgəni (1 m² qiymətinə görə) və niyə, büdcəyə uyğun elan sayını. "
     "Hər rayonun statusu sətirdə yazılıb: 'məlumat azdır' ifadəsini YALNIZ statusu 'məlumat azdır' olan rayonlar üçün işlət; "
     "statusu 'kifayət' olan rayon haqqında heç vaxt 'məlumat azdır' yazma. Sahibkar üçün praktik ol."),
    ("human", "Sorğu: {ptype}, sahə {area}, büdcə {budget}.\nRayonlar:\n{rows}\nƏn sərfəli: {best}"),
])


def _rows_text(summaries: list[DistrictSummary]) -> str:
    lines = []
    for s in summaries:
        if s.n == 0:
            lines.append(f"- {s.district}: elan yoxdur")
        elif not s.enough_data:
            lines.append(f"- {s.district}: status=məlumat azdır, {s.n} elan, median {s.median_price:.0f} AZN")
        else:
            lines.append(f"- {s.district}: status=kifayət, {s.n} elan, median {s.median_price:.0f} AZN, "
                         f"1 m² {s.median_price_per_m2:.1f} AZN, büdcəyə uyğun {s.within_budget}")
    return "\n".join(lines)


def template_summary(q: AreaQuery, summaries: list[DistrictSummary], best: str | None) -> str:
    ok = [s for s in summaries if s.enough_data]
    few = [s.district for s in summaries if not s.enough_data]
    if not ok:
        return "Seçilmiş rayonlarda bu filtrlərlə kifayət qədər elan yoxdur. Sahə aralığını genişləndir və ya başqa rayon seç."
    if best:
        b = next(s for s in ok if s.district == best)
        text = (f"Ən sərfəli bölgə {best}-dir: 1 m² medianı {b.median_price_per_m2:.1f} AZN, "
                f"median aylıq qiymət {b.median_price:.0f} AZN.")
        if q.budget_azn is not None:
            text += f" Büdcənə ({q.budget_azn} AZN) uyğun {b.within_budget} elan var."
    else:
        text = "Büdcəyə uyğun elanı olan, kifayət qədər məlumatlı rayon tapılmadı."
    if few:
        text += f" {', '.join(few)} üçün məlumat azdır."
    return text


def summary_is_consistent(text: str, summaries: list[DistrictSummary], best: str | None) -> bool:
    """Guard against LLM claims that contradict the computed table."""
    low = text.lower()
    if "məlumat az" in low and all(s.enough_data for s in summaries if s.n > 0):
        return False
    if best and best.lower()[:5] not in low:
        return False
    return True


def write_area_summary(q: AreaQuery, summaries: list[DistrictSummary], best: str | None, llm=None) -> tuple[str, int, int, float, bool]:
    if llm is None and not llm_available():
        return template_summary(q, summaries, best), 0, 0, 0.0, False
    from src.agent import get_llm, usage_cost

    cb = UsageMetadataCallbackHandler()
    try:
        chain = (AREA_PROMPT | (llm or get_llm()).with_structured_output(AreaSummaryOut)).with_config(
            run_name="write_area_summary")
        area = f"{q.area_min or 0:g}–{q.area_max:g} m²" if q.area_max else "istənilən"
        out = chain.invoke({
            "ptype": q.property_type, "area": area,
            "budget": f"{q.budget_azn} AZN" if q.budget_azn else "verilməyib",
            "rows": _rows_text(summaries), "best": best or "yoxdur",
        }, config={"callbacks": [cb]})
        tin, tout, cost = usage_cost(cb.usage_metadata)
        if not summary_is_consistent(out.summary, summaries, best):
            return template_summary(q, summaries, best), tin, tout, cost, True
        return out.summary, tin, tout, cost, True
    except Exception:
        return template_summary(q, summaries, best), 0, 0, 0.0, False


@traceable(name="edaletli_kiraye_area", tags=["edaletli-kiraye", "area"])
def build_area_report(q: AreaQuery, live: bool = False, llm=None,
                      on_step: Callable[[str], None] | None = None) -> AreaResult:
    say = on_step or (lambda _s: None)
    if live:
        refresh_live(q, say)
    say(f"{len(q.districts)} rayon paralel müqayisə olunur…")
    by_district = gather_districts(q)
    summaries, best = compare_districts(q, by_district)
    say("Rayonlar müqayisə edildi" + (f" — ən sərfəli: {best}" if best else ""))
    say("Xülasə yazılır…")
    summary, tin, tout, cost, used = write_area_summary(q, summaries, best, llm=llm)
    report = AreaReport(query=q, districts=summaries, best_value_district=best,
                        matching_listings=matching_listings(q, by_district, best), summary=summary)
    run = get_current_run_tree()
    if run is not None:
        run.add_metadata({"property_type": q.property_type, "districts": q.districts,
                          "best_value_district": best, "budget_azn": q.budget_azn, "cost_usd": cost})
    return AreaResult(report=report, listings_by_district=by_district, input_tokens=tin,
                      output_tokens=tout, cost_usd=cost, used_llm=used)
