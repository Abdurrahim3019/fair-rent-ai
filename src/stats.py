"""Pure-Python market statistics and deterministic verdict logic (no LLM)."""
from statistics import median

from src.config import MIN_COMPARABLES, fair_threshold
from src.models import AreaQuery, DistrictSummary, Listing, MarketStats, Verdict


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile (same as numpy default), q in [0, 100]."""
    if not values:
        raise ValueError("percentile of empty list")
    data = sorted(values)
    if len(data) == 1:
        return float(data[0])
    pos = (len(data) - 1) * q / 100
    lo = int(pos)
    hi = min(lo + 1, len(data) - 1)
    return float(data[lo] + (data[hi] - data[lo]) * (pos - lo))


def percentile_rank(values: list[float], x: float) -> float:
    """Share of values strictly below x plus half of ties, in 0–100."""
    if not values:
        raise ValueError("percentile_rank of empty list")
    below = sum(1 for v in values if v < x)
    equal = sum(1 for v in values if v == x)
    return 100 * (below + 0.5 * equal) / len(values)


def iqr_filter(values: list[float], k: float = 1.5) -> list[float]:
    """Drop outliers outside [Q1 - k*IQR, Q3 + k*IQR]. Skipped for small samples."""
    if len(values) < 4:
        return list(values)
    q1, q3 = percentile(values, 25), percentile(values, 75)
    iqr = q3 - q1
    lo, hi = q1 - k * iqr, q3 + k * iqr
    return [v for v in values if lo <= v <= hi]


def compute_market_stats(
    prices: list[float], areas: list[float], target_price: float | None = None
) -> MarketStats:
    if not prices:
        raise ValueError("no prices")
    if len(prices) != len(areas):
        raise ValueError("prices and areas must have the same length")
    per_m2 = [p / a for p, a in zip(prices, areas) if a > 0]
    return MarketStats(
        n=len(prices),
        median_price=float(median(prices)),
        p25=percentile(prices, 25),
        p75=percentile(prices, 75),
        median_price_per_m2=round(float(median(per_m2)), 2) if per_m2 else 0.0,
        listing_percentile=(
            round(percentile_rank(prices, target_price), 1)
            if target_price is not None
            else None
        ),
    )


def compute_verdict(
    listing_price: int,
    stats: MarketStats | None,
    property_type: str = "mənzil",
    user_guess: int | None = None,
) -> Verdict:
    """Deterministic verdict. Explanation/message are filled later by the LLM."""
    if stats is None or stats.n < MIN_COMPARABLES:
        fair = (int(stats.p25), int(stats.p75)) if stats else (0, 0)
        diff = int(listing_price - stats.median_price) if stats else 0
        pct = (diff / stats.median_price * 100) if stats and stats.median_price else 0.0
        return Verdict(
            label="məlumat azdır",
            diff_azn=diff,
            diff_pct=round(pct, 1),
            fair_range=fair,
            user_guess=user_guess,
        )

    diff = listing_price - stats.median_price
    pct = diff / stats.median_price
    threshold = fair_threshold(property_type)
    if pct > threshold:
        label = "baha"
    elif pct < -threshold:
        label = "ucuz"
    else:
        label = "ədalətli"
    return Verdict(
        label=label,
        diff_azn=round(diff),
        diff_pct=round(pct * 100, 1),
        fair_range=(round(stats.p25), round(stats.p75)),
        user_guess=user_guess,
    )


def summarize_district(
    district: str, listings: list[Listing], budget_azn: int | None = None
) -> DistrictSummary:
    n = len(listings)
    if n == 0:
        return DistrictSummary(district=district, n=0, enough_data=False)
    prices = [float(x.price_azn) for x in listings]
    per_m2 = [x.price_per_m2 for x in listings]
    within = sum(1 for p in prices if budget_azn is not None and p <= budget_azn)
    return DistrictSummary(
        district=district,
        n=n,
        median_price=float(median(prices)),
        p25=percentile(prices, 25),
        p75=percentile(prices, 75),
        median_price_per_m2=round(float(median(per_m2)), 2),
        within_budget=within,
        enough_data=n >= MIN_COMPARABLES,
    )


def filter_for_query(listings: list[Listing], q: AreaQuery) -> list[Listing]:
    out = []
    for x in listings:
        if x.property_type != q.property_type:
            continue
        if q.area_min is not None and x.area_m2 < q.area_min:
            continue
        if q.area_max is not None and x.area_m2 > q.area_max:
            continue
        if q.rooms is not None and x.rooms is not None and x.rooms != q.rooms:
            continue
        out.append(x)
    return out


def compare_districts(
    q: AreaQuery, listings_by_district: dict[str, list[Listing]]
) -> tuple[list[DistrictSummary], str | None]:
    """Summaries sorted by median price per m² (cheapest first) and best-value district."""
    summaries = [
        summarize_district(d, filter_for_query(listings_by_district.get(d, []), q), q.budget_azn)
        for d in q.districts
    ]

    def sort_key(s: DistrictSummary):
        return (not s.enough_data, s.median_price_per_m2 if s.median_price_per_m2 is not None else float("inf"))

    summaries.sort(key=sort_key)
    eligible = [s for s in summaries if s.enough_data]
    if q.budget_azn is not None:
        eligible = [s for s in eligible if s.within_budget > 0]
    best = eligible[0].district if eligible else None
    return summaries, best
