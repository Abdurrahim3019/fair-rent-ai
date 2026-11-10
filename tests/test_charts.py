from src.charts import market_range_chart
from src.models import MarketStats, Verdict


def _spec(guess=None):
    s = MarketStats(n=6, median_price=875, p25=850, p75=900, median_price_per_m2=7.7)
    v = Verdict(label="ədalətli", diff_azn=-25, diff_pct=-2.9, fair_range=(850, 900), user_guess=guess)
    return market_range_chart(s, v, 850, [799, 850, 850, 900, 900, 1250]).to_dict()


def test_chart_layers_and_labels():
    spec = _spec(guess=700)
    assert len(spec["layer"]) == 7
    text = str(spec)
    assert "Elanın qiyməti" in text and "Sənin təxminin" in text and "median 875" in text and "ədalətli aralıq 850–900" in text


def test_chart_without_guess():
    assert "Sənin təxminin" not in str(_spec())
