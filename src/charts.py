"""Market-range strip chart (Altair).

One horizontal price axis: p25–p75 band, median rule, comparable prices as
small neutral dots, the listing price and the user's guess as labelled markers.
Colors: reference palette categorical slots 1 (blue) and 2 (orange), stepped per
theme; identity is also carried by marker shape + direct label (not color alone).
"""
import altair as alt
import pandas as pd

from src.models import MarketStats, Verdict

THEMES = {
    "light": {"listing": "#2a78d6", "guess": "#eb6834", "band": "#d9d8d3", "dot": "#8a8984",
              "text": "#0b0b0b", "muted": "#52514e"},
    "dark": {"listing": "#3987e5", "guess": "#d95926", "band": "#3a3a37", "dot": "#8f8e86",
             "text": "#f0efec", "muted": "#c3c2b7"},
}


def market_range_chart(
    stats: MarketStats,
    verdict: Verdict,
    listing_price: int,
    comparable_prices: list[int],
    theme: str = "dark",
) -> alt.LayerChart:
    c = THEMES.get(theme, THEMES["dark"])
    lo, hi = verdict.fair_range
    values = [listing_price, lo, hi, stats.median_price, *comparable_prices]
    if verdict.user_guess:
        values.append(verdict.user_guess)
    pad = max(50, (max(values) - min(values)) * 0.08)
    domain = [max(0, min(values) - pad), max(values) + pad]
    x = alt.X("price:Q", title="Aylıq qiymət (AZN)", scale=alt.Scale(domain=domain, nice=False),
              axis=alt.Axis(grid=False, tickCount=6, labelColor=c["muted"], titleColor=c["muted"]))

    # Vertical layout (px from top): marker labels 10/26 · band 42–86 · marks 64 · bottom labels 102 · axis below.
    band = alt.Chart(pd.DataFrame([{"lo": lo, "hi": hi}])).mark_rect(
        color=c["band"], cornerRadius=4, y=42, y2=86,
    ).encode(
        x=alt.X("lo:Q", scale=alt.Scale(domain=domain, nice=False)), x2="hi:Q",
        tooltip=[alt.Tooltip("lo:Q", title="Aralığın aşağısı (p25)"), alt.Tooltip("hi:Q", title="Aralığın yuxarısı (p75)")],
    )
    band_label = alt.Chart(pd.DataFrame([{"price": hi, "t": f"  ədalətli aralıq {lo}–{hi}"}])).mark_text(
        y=102, fontSize=11, color=c["muted"], align="left",
    ).encode(x=x, text="t:N")

    dots = alt.Chart(pd.DataFrame({"price": comparable_prices})).mark_circle(
        size=40, color=c["dot"], opacity=0.9, y=64,
    ).encode(x=x, tooltip=[alt.Tooltip("price:Q", title="Oxşar elan, AZN")])

    median = alt.Chart(pd.DataFrame([{"price": stats.median_price}])).mark_rule(
        color=c["text"], strokeWidth=2, y=38, y2=90,
    ).encode(x=x, tooltip=[alt.Tooltip("price:Q", title="Median, AZN", format=".0f")])
    median_label = alt.Chart(pd.DataFrame([{"price": stats.median_price, "t": f"median {stats.median_price:.0f}  "}])).mark_text(
        y=102, fontSize=11, color=c["text"], align="right",
    ).encode(x=x, text="t:N")

    markers = [{"price": listing_price, "who": "Elanın qiyməti", "shape": "diamond", "color": c["listing"], "ty": 10}]
    if verdict.user_guess:
        markers.append({"price": verdict.user_guess, "who": "Sənin təxminin", "shape": "triangle-up",
                        "color": c["guess"], "ty": 10 if abs(verdict.user_guess - listing_price) > (domain[1] - domain[0]) * 0.25 else 26})
    mdf = pd.DataFrame(markers)
    pts = alt.Chart(mdf).mark_point(filled=True, size=220, y=64, stroke=c["text"], strokeWidth=1.5).encode(
        x=x,
        shape=alt.Shape("who:N", scale=alt.Scale(domain=mdf["who"].tolist(), range=mdf["shape"].tolist()), legend=None),
        color=alt.Color("who:N", scale=alt.Scale(domain=mdf["who"].tolist(), range=mdf["color"].tolist()), legend=None),
        tooltip=[alt.Tooltip("who:N", title=""), alt.Tooltip("price:Q", title="AZN")],
    )
    labels = alt.Chart(mdf).mark_text(fontSize=12, fontWeight="bold", color=c["text"]).encode(
        x=x, y=alt.Y("ty:Q", scale=None), text=alt.Text("label:N"),
    ).transform_calculate(label="datum.who + ': ' + datum.price")

    return (
        alt.layer(band, band_label, dots, median, median_label, pts, labels)
        .properties(height=165)  # total incl. x axis (~45px); marks use y<=110
        .configure_view(stroke=None)
        .configure_axis(domainColor=c["muted"], tickColor=c["muted"])
    )


def district_bar_chart(summaries, budget_azn: int | None, best: str | None, theme: str = "dark") -> alt.LayerChart:
    """Horizontal bars: median monthly price per district (one axis, AZN), budget as a rule.

    Districts with too little data are drawn in the neutral color and labelled, so
    identity is never color-only.
    """
    c = THEMES.get(theme, THEMES["dark"])
    rows = [
        {
            "district": s.district + (" 🏆" if s.district == best else ""),
            "median": s.median_price,
            "per_m2": s.median_price_per_m2,
            "n": s.n,
            "within": s.within_budget,
            "status": "kifayət" if s.enough_data else "məlumat azdır",
            "label": f"{s.median_price:.0f} AZN · {s.median_price_per_m2:.1f}/m²" if s.enough_data
            else f"{s.median_price:.0f} AZN · məlumat azdır",
        }
        for s in summaries if s.n > 0
    ]
    df = pd.DataFrame(rows)
    if df.empty:
        return alt.layer(alt.Chart(pd.DataFrame({"x": []})).mark_point())
    order = df["district"].tolist()  # already sorted by price per m² (cheapest first)
    xmax = max(df["median"].max(), budget_azn or 0) * 1.35
    y = alt.Y("district:N", sort=order, title=None,
              axis=alt.Axis(labelColor=c["text"], labelFontSize=12, ticks=False, domain=False))
    x = alt.X("median:Q", title="Median aylıq qiymət (AZN)", scale=alt.Scale(domain=[0, xmax]),
              axis=alt.Axis(grid=False, labelColor=c["muted"], titleColor=c["muted"]))
    bars = alt.Chart(df).mark_bar(cornerRadiusEnd=4, height={"band": 0.6}).encode(
        y=y, x=x,
        color=alt.Color("status:N", scale=alt.Scale(domain=["kifayət", "məlumat azdır"],
                                                    range=[c["listing"], c["band"]]), legend=None),
        tooltip=[alt.Tooltip("district:N", title="Rayon"), alt.Tooltip("median:Q", title="Median, AZN", format=".0f"),
                 alt.Tooltip("per_m2:Q", title="1 m², AZN", format=".1f"), alt.Tooltip("n:Q", title="Elan sayı"),
                 alt.Tooltip("within:Q", title="Büdcəyə uyğun")],
    )
    text = alt.Chart(df).mark_text(align="left", dx=6, fontSize=11, color=c["muted"]).encode(y=y, x=x, text="label:N")
    layers = [bars, text]
    if budget_azn:
        bdf = pd.DataFrame([{"median": budget_azn, "t": f"büdcə {budget_azn}"}])
        layers.append(alt.Chart(bdf).mark_rule(color=c["guess"], strokeWidth=2, strokeDash=[6, 4]).encode(x=x))
        layers.append(alt.Chart(bdf).mark_text(color=c["guess"], fontSize=11, fontWeight="bold", y=-8, dx=4,
                                                align="left").encode(x=x, text="t:N"))
    return (
        alt.layer(*layers)
        .properties(height=max(120, 42 * len(df)) + 40)
        .configure_view(stroke=None)
        .configure_axis(domainColor=c["muted"], tickColor=c["muted"])
    )
