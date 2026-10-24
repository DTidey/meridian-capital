"""Long and short book targets, checked against every configured exposure bound."""

from __future__ import annotations

_TOLERANCE = 1e-9


def book_targets(config: dict) -> tuple[float, float]:
    """Return (long_gross, short_gross); raise ValueError if any configured bound disagrees.

    Bounds are checked only when present: portfolio.gross_exposure / net_exposure_min /
    net_exposure_max, and risk.pre_trade.max_gross / net_min / net_max (a target book outside
    the pre-trade limits could never pass the veto).
    """
    pcfg = config.get("portfolio", {})
    pre_trade = config.get("risk", {}).get("pre_trade", {})
    long_gross = float(pcfg.get("target_long_gross", 0.80))
    short_gross = float(pcfg.get("target_short_gross", 0.70))
    if long_gross < 0 or short_gross < 0:
        raise ValueError(f"book targets must not be negative: {long_gross}/{short_gross}")

    gross, net = long_gross + short_gross, long_gross - short_gross
    if "gross_exposure" in pcfg and abs(gross - float(pcfg["gross_exposure"])) > _TOLERANCE:
        raise ValueError(f"targets gross {gross:.2f} != portfolio.gross_exposure")
    if "max_gross" in pre_trade and gross > float(pre_trade["max_gross"]) + _TOLERANCE:
        raise ValueError(f"targets gross {gross:.2f} > risk.pre_trade.max_gross")
    for key, section, low in (
        ("net_exposure_min", pcfg, True),
        ("net_exposure_max", pcfg, False),
        ("net_min", pre_trade, True),
        ("net_max", pre_trade, False),
    ):
        if key not in section:
            continue
        bound = float(section[key])
        if (net < bound - _TOLERANCE) if low else (net > bound + _TOLERANCE):
            raise ValueError(f"targets net {net:+.2f} outside {key} {bound:+.2f}")
    return long_gross, short_gross
