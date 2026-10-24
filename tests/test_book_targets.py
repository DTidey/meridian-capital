"""Packet 51 (H4): book targets agree with every configured exposure bound."""

from pathlib import Path

import pytest
import yaml

import portfolio.mvo_optimizer
import portfolio.optimizer
from portfolio.book_targets import book_targets

_CFG = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())


def _config(long=0.80, short=0.70, **bounds):
    portfolio_keys = {"gross_exposure", "net_exposure_min", "net_exposure_max"}
    pcfg = {"target_long_gross": long, "target_short_gross": short}
    pcfg.update({k: v for k, v in bounds.items() if k in portfolio_keys})
    pre_trade = {k: v for k, v in bounds.items() if k not in portfolio_keys}
    return {"portfolio": pcfg, "risk": {"pre_trade": pre_trade}}


def test_production_config_consistent():
    assert book_targets(_CFG) == pytest.approx((0.80, 0.70))
    pcfg = _CFG["portfolio"]
    assert pcfg["target_long_gross"] + pcfg["target_short_gross"] == pytest.approx(
        pcfg["gross_exposure"]
    )


class TestBounds:
    def test_gross_must_match_gross_exposure(self):
        with pytest.raises(ValueError, match="gross_exposure"):
            book_targets(_config(gross_exposure=1.60))

    def test_net_above_portfolio_max(self):
        # The pre-packet config: +0.30 net against a 0.10 maximum.
        with pytest.raises(ValueError, match="net_exposure_max"):
            book_targets(_config(0.90, 0.60, net_exposure_max=0.10))

    def test_net_below_portfolio_min(self):
        with pytest.raises(ValueError, match="net_exposure_min"):
            book_targets(_config(0.70, 0.80, net_exposure_min=0.0))

    def test_net_outside_pre_trade(self):
        # H4 as found: the target book could never pass the pre-trade net veto.
        with pytest.raises(ValueError, match="net_max"):
            book_targets(_config(0.90, 0.60, net_max=0.15))
        with pytest.raises(ValueError, match="net_min"):
            book_targets(_config(0.50, 0.80, net_min=-0.10))

    def test_gross_above_pre_trade_max(self):
        with pytest.raises(ValueError, match="max_gross"):
            book_targets(_config(0.90, 0.80, max_gross=1.65))

    def test_negative_target(self):
        with pytest.raises(ValueError, match="negative"):
            book_targets(_config(0.80, -0.10))

    def test_net_on_the_bound_allowed(self):
        assert book_targets(
            _config(net_exposure_max=0.10, net_max=0.10, gross_exposure=1.50)
        ) == pytest.approx((0.80, 0.70))

    def test_absent_bounds_not_checked(self):
        # Adversarial: partial configs (tests, scripts) check only the signs.
        assert book_targets({"portfolio": {"target_long_gross": 0.9}}) == pytest.approx(
            (0.90, 0.70)
        )

    def test_defaults_are_checked_too(self):
        # Adversarial: omitted targets fall back to 0.80/0.70 and still meet the bounds.
        with pytest.raises(ValueError, match="gross_exposure"):
            book_targets({"portfolio": {"gross_exposure": 1.20}})


def test_optimisers_use_book_targets(monkeypatch):
    calls = []

    def fake(config):
        calls.append(config)
        raise RuntimeError("stop after targets")

    monkeypatch.setattr(portfolio.optimizer, "book_targets", fake)
    monkeypatch.setattr(portfolio.mvo_optimizer, "book_targets", fake)
    for module in (portfolio.optimizer, portfolio.mvo_optimizer):
        calls.clear()
        with pytest.raises(RuntimeError, match="stop after targets"):
            module.optimise(None, {}, None, {"portfolio": {}}, "2026-10-26", None)
        assert calls, module.__name__


def test_mvo_fallback_does_not_hide_bad_targets():
    # Adversarial: MVO falls back to the conviction optimiser on any error; the fallback
    # checks the targets too, so an inconsistent config still stops Layer 4.
    bad = {"portfolio": {"target_long_gross": 0.90, "target_short_gross": 0.60}}
    bad["risk"] = {"pre_trade": {"net_max": 0.15}}
    with pytest.raises(ValueError, match="net_max"):
        portfolio.mvo_optimizer.optimise(None, {}, None, bad, "2026-10-26", None)
