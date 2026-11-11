"""yfinance no longer uses pandas `inplace` (packet 65): the root cause is fixed, not hidden."""

import ast
import subprocess  # nosec B404 - runs this interpreter on a fixed snippet
import sys
import warnings
from pathlib import Path

import pandas as pd
import pytest
import yfinance as yf
from packaging.requirements import Requirement
from pandas.errors import Pandas4Warning
from yfinance.cache import _TzCacheDummy, _TzCacheManager
from yfinance.data import YfData

import data.market_data as market_data

_REPO = Path(__file__).resolve().parents[1]
_DAYS = ["2026-10-19", "2026-10-20", "2026-10-21"]
_START, _END = "2026-10-19", "2026-10-22"


# ---------------------------------------------------------------------------
# Static scan of the installed yfinance source
# ---------------------------------------------------------------------------


def _inplace_calls(source: str, name: str = "<src>") -> list[str]:
    """`file:line` of every call passing `inplace`, directly or via a `**{...}` literal."""
    hits = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            unpacked = kw.arg is None and isinstance(kw.value, ast.Dict)
            keys = (
                [k.value for k in kw.value.keys if isinstance(k, ast.Constant)] if unpacked else []
            )
            if kw.arg == "inplace" or "inplace" in keys:
                hits.append(f"{name}:{node.lineno}")
    return hits


def _yfinance_sources() -> list[Path]:
    return sorted(Path(yf.__file__).parent.rglob("*.py"))


def test_installed_yfinance_has_no_inplace_calls():
    files = _yfinance_sources()
    assert len(files) > 10  # not a vacuous pass over an empty or wrong directory
    hits = [h for f in files for h in _inplace_calls(f.read_text(encoding="utf-8"), f.name)]
    assert hits == []


def test_installed_yfinance_at_least_1_8():
    major, minor = (int(p) for p in yf.__version__.split(".")[:2])
    assert (major, minor) >= (1, 8)


def test_requirements_pin_rules_out_pre_1_8():
    lines = (_REPO / "requirements.in").read_text(encoding="utf-8").splitlines()
    reqs = [Requirement(s) for s in lines if s.strip().lower().startswith("yfinance")]
    assert len(reqs) == 1
    spec = reqs[0].specifier
    assert "1.8.0" in spec
    assert not any(v in spec for v in ("1.7.0", "1.7.9", "1.0.0", "0.2.66"))


def test_scanner_flags_literal_inplace():
    assert _inplace_calls("df.sort_index(inplace=True)\n") == ["<src>:1"]


def test_scanner_flags_indirect_inplace():
    src = "df.rename(columns=m, inplace=flag)\ndf.set_index('a', **{'inplace': True})\n"
    assert _inplace_calls(src) == ["<src>:1", "<src>:2"]


def test_scanner_ignores_non_call_mentions():
    src = "# df.sort_index(inplace=True)\nx = 'inplace=True'\ninplace = True\nf(x=inplace)\n"
    assert _inplace_calls(src) == []


# ---------------------------------------------------------------------------
# Layer 1 download path through real yfinance, HTTP stubbed
# ---------------------------------------------------------------------------


def _chart(ticker: str, close: float) -> dict:
    stamps = [int(pd.Timestamp(f"{d} 13:30", tz="UTC").timestamp()) for d in _DAYS]
    n = len(stamps)
    quote = {k: [close] * n for k in ("open", "high", "low", "close")}
    quote["volume"] = [1000] * n
    meta = {
        "currency": "USD",
        "symbol": ticker,
        "exchangeName": "NMS",
        "instrumentType": "EQUITY",
        "gmtoffset": -14400,
        "timezone": "EDT",
        "exchangeTimezoneName": "America/New_York",
        "dataGranularity": "1d",
        "range": "",
        "validRanges": ["1d", "5d", "1mo", "max"],
    }
    events = {
        "dividends": {str(stamps[1]): {"amount": 0.5, "date": stamps[1]}},
        "splits": {str(stamps[2]): {"date": stamps[2], "numerator": 2, "denominator": 1}},
    }
    result = {
        "meta": meta,
        "timestamp": stamps,
        "events": events,
        "indicators": {"quote": [quote], "adjclose": [{"adjclose": [close - 1] * n}]},
    }
    return {"chart": {"result": [result], "error": None}}


class _Response:
    def __init__(self, payload: dict, on_json=None):
        self._payload, self._on_json = payload, on_json
        self.text, self.status_code = "{}", 200

    def json(self):
        if self._on_json:
            self._on_json()
        return self._payload


@pytest.fixture
def stub_yahoo(monkeypatch):
    """Serve canned chart JSON to every yfinance GET; no network, no tz cache on disk."""
    closes = {"AAA": 100.0, "BBB": 50.0}
    state = {"on_json": None}

    def get(self, url, params=None, timeout=30):
        ticker = url.rsplit("/", 1)[-1]
        return _Response(_chart(ticker, closes[ticker]), state["on_json"])

    monkeypatch.setattr(YfData, "get", get)
    monkeypatch.setattr(YfData, "cache_get", get)
    monkeypatch.setattr(_TzCacheManager, "get_tz_cache", classmethod(lambda cls: _TzCacheDummy()))
    return state


def _deprecations(fn):
    """Run fn recording every warning; return (result, pandas/Future/Deprecation warnings)."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = fn()
    bad = (Pandas4Warning, FutureWarning, DeprecationWarning)
    return out, [w for w in caught if issubclass(w.category, bad)]


def test_batch_download_raises_no_deprecations(stub_yahoo):
    out, found = _deprecations(
        lambda: market_data._fetch_yfinance_batch(["AAA", "BBB"], _START, _END)
    )
    assert [str(w.message) for w in found] == []
    assert sorted(out) == ["AAA", "BBB"]
    assert list(out["AAA"]["Close"]) == [100.0] * 3
    assert list(out["BBB"]["Adj Close"]) == [49.0] * 3


def test_single_download_raises_no_deprecations(stub_yahoo):
    out, found = _deprecations(lambda: market_data._fetch_yfinance_single("AAA", _START, _END))
    assert [str(w.message) for w in found] == []
    assert len(out) == 3
    assert list(out["Close"]) == [100.0] * 3


def test_harness_catches_pandas4_warning_inside_yfinance(stub_yahoo):
    """A Pandas4Warning raised mid-download (worker thread) must be reported, not lost."""

    def warn():
        warnings.warn("The inplace keyword", Pandas4Warning, stacklevel=2)

    stub_yahoo["on_json"] = warn
    _, found = _deprecations(
        lambda: market_data._fetch_yfinance_batch(["AAA", "BBB"], _START, _END)
    )
    assert found and all(w.category is Pandas4Warning for w in found)


# ---------------------------------------------------------------------------
# The filter is gone
# ---------------------------------------------------------------------------


def test_filter_function_removed():
    assert not hasattr(market_data, "ignore_yfinance_inplace_warnings")


def test_import_installs_no_yfinance_pandas4_filter():
    """Fresh interpreter: importing Layer 1 adds no warnings filter aimed at yfinance."""
    snippet = (
        "import warnings, data.market_data\n"
        "from pandas.errors import Pandas4Warning\n"
        "print(sum(1 for a, m, c, mod, _ in warnings.filters\n"
        "          if a == 'ignore' and issubclass(Pandas4Warning, c)\n"
        "          and mod is not None and 'yfinance' in mod.pattern))\n"
    )
    out = subprocess.run(  # nosec B603 - fixed argv, no shell
        [sys.executable, "-c", snippet], cwd=_REPO, capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "0"
