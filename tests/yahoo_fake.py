"""In-memory stand-in for yfinance.Ticker: scripted .info / .calendar / earningsTrend responses,
counted calls."""

from collections import Counter

import pandas as pd

_TREND_COLUMNS = ("current", "7daysAgo", "30daysAgo", "60daysAgo", "90daysAgo")


class FakeYahoo:
    """Call as yf.Ticker. Each script maps ticker -> list of responses; the last one repeats.

    A response that is an Exception instance is raised; anything else is returned.
    Tickers missing from a script get the default response.
    A "trend" response is a {period: {column: value}} dict (see trend()); like yfinance, one
    request per Ticker object serves both eps_trend and earnings_estimate.
    """

    def __init__(
        self,
        info=None,
        calendar=None,
        default_info=None,
        default_calendar=None,
        trend=None,
        default_trend=None,
    ):
        self.scripts = {"info": info or {}, "calendar": calendar or {}, "trend": trend or {}}
        self.defaults = {"info": default_info, "calendar": default_calendar, "trend": default_trend}
        self.calls = {"info": Counter(), "calendar": Counter(), "trend": Counter()}

    def __call__(self, ticker):
        return _FakeTicker(self, ticker)

    def respond(self, attr, ticker):
        n = self.calls[attr][ticker]
        self.calls[attr][ticker] += 1
        script = self.scripts[attr].get(ticker)
        value = script[min(n, len(script) - 1)] if script else self.defaults[attr]
        if isinstance(value, Exception):
            raise value
        return value


class _FakeTicker:
    def __init__(self, fake, ticker):
        self._fake = fake
        self._ticker = ticker
        self._trend = None

    @property
    def info(self):
        return self._fake.respond("info", self._ticker)

    @property
    def calendar(self):
        return self._fake.respond("calendar", self._ticker)

    def _earnings_trend(self) -> dict:
        if self._trend is None:
            self._trend = self._fake.respond("trend", self._ticker) or {}
        return self._trend

    @property
    def eps_trend(self):
        return _frame(self._earnings_trend(), _TREND_COLUMNS)

    @property
    def earnings_estimate(self):
        return _frame(self._earnings_trend(), ("avg",))


def _frame(by_period: dict, columns) -> pd.DataFrame:
    """yfinance's shape: indexed by period, numeric columns plus a 'currency' string column."""
    rows = [
        {"period": p, **{c: v[c] for c in columns if c in v}} for p, v in by_period.items()
    ]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).set_index("period")
    df["currency"] = "USD"
    return df


def trend(current, d7=None, d30=None, d60=None, d90=None, avg=None) -> dict:
    """One period's earningsTrend values; None leaves the column out."""
    values = zip(("avg", *_TREND_COLUMNS), (avg, current, d7, d30, d60, d90), strict=True)
    return {k: v for k, v in values if v is not None}


INFO = {
    "sharesShort": 1_000_000,
    "shortRatio": 2.5,
    "shortPercentOfFloat": 0.03,
    "forwardEps": 7.5,
    "targetMeanPrice": 250.0,
    "numberOfAnalystOpinions": 30,
}

# PGR on 2026-10-21 (yfinance 1.7.0); 0q avg equals .calendar's "Earnings Average".
PGR_TREND = {
    "0q": trend(4.11543, avg=4.11543),
    "+1q": trend(4.2, avg=4.2),
    "0y": trend(15.1, d7=15.12, d30=15.0, d60=14.8, d90=14.5, avg=15.1),
    "+1y": trend(16.30119, d7=16.36848, d30=16.27934, d60=16.0, d90=15.8, avg=16.30119),
}
