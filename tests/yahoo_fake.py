"""In-memory stand-in for yfinance.Ticker: scripted .info / .calendar responses, counted calls."""

from collections import Counter


class FakeYahoo:
    """Call as yf.Ticker. Each script maps ticker -> list of responses; the last one repeats.

    A response that is an Exception instance is raised; anything else is returned.
    Tickers missing from a script get the default response.
    """

    def __init__(self, info=None, calendar=None, default_info=None, default_calendar=None):
        self.scripts = {"info": info or {}, "calendar": calendar or {}}
        self.defaults = {"info": default_info, "calendar": default_calendar}
        self.calls = {"info": Counter(), "calendar": Counter()}

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

    @property
    def info(self):
        return self._fake.respond("info", self._ticker)

    @property
    def calendar(self):
        return self._fake.respond("calendar", self._ticker)


INFO = {
    "sharesShort": 1_000_000,
    "shortRatio": 2.5,
    "shortPercentOfFloat": 0.03,
    "forwardEps": 7.5,
    "targetMeanPrice": 250.0,
    "numberOfAnalystOpinions": 30,
}
