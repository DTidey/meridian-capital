"""Transaction cost model: spread + market impact (Alpaca zero commission)."""

import math

import pandas as pd


def estimate_cost(
    ticker: str,
    trade_shares: float,
    price: float,
    prices_df: pd.DataFrame,
    config: dict,
) -> float:
    """Return estimated total transaction cost in USD for one trade.

    Args:
        ticker: Ticker symbol (used only for logging).
        trade_shares: Absolute number of shares traded.
        price: Current price per share.
        prices_df: DataFrame with columns [high, low, close, volume] for
                   the last adv_lookback_days days for this ticker.
        config: Full application config dict.
    """
    tc_cfg = config.get("portfolio", {}).get("transaction_costs", {})
    hl_frac = tc_cfg.get("spread_hl_fraction", 0.05)
    impact_coef = tc_cfg.get("market_impact_coef", 0.10)
    adv_days = config.get("portfolio", {}).get("adv_lookback_days", 20)

    trade_value = abs(trade_shares) * price

    spread_cost = _spread_cost(prices_df, hl_frac, trade_value, adv_days)
    impact_cost = _market_impact(prices_df, trade_shares, price, impact_coef, adv_days)

    return spread_cost + impact_cost


def net_expected_return(
    gross_return: float,
    cost_usd: float,
    position_value: float,
) -> float:
    """Deduct proportional transaction cost from expected annual return."""
    if position_value <= 0:
        return gross_return
    cost_as_return = cost_usd / position_value
    return gross_return - cost_as_return


def compute_adv(prices_df: pd.DataFrame, adv_days: int) -> float:
    """Average daily value traded (shares × close) over adv_days."""
    if prices_df.empty or "volume" not in prices_df.columns:
        return 0.0
    recent = prices_df.tail(adv_days)
    adv_shares = recent["volume"].mean()
    avg_price = recent["close"].mean() if "close" in recent.columns else 1.0
    return float(adv_shares * avg_price) if not math.isnan(adv_shares * avg_price) else 0.0


def _spread_cost(
    prices_df: pd.DataFrame,
    hl_frac: float,
    trade_value: float,
    adv_days: int,
) -> float:
    if prices_df.empty or "high" not in prices_df.columns or "low" not in prices_df.columns:
        return 0.0
    recent = prices_df.tail(adv_days)
    close_col = "close" if "close" in recent.columns else "adj_close"
    if close_col not in recent.columns or recent[close_col].mean() == 0:
        return 0.0
    avg_hl = (recent["high"] - recent["low"]).mean()
    avg_close = recent[close_col].mean()
    spread_pct = hl_frac * avg_hl / avg_close
    return spread_pct * trade_value


def _market_impact(
    prices_df: pd.DataFrame,
    trade_shares: float,
    price: float,
    coef: float,
    adv_days: int,
) -> float:
    if prices_df.empty or "volume" not in prices_df.columns:
        return 0.0
    recent = prices_df.tail(adv_days)
    adv_shares = recent["volume"].mean()
    if adv_shares <= 0 or math.isnan(adv_shares):
        return 0.0

    close_col = "close" if "close" in recent.columns else "adj_close"
    if close_col not in recent.columns:
        return 0.0
    returns = recent[close_col].pct_change().dropna()
    if returns.empty:
        return 0.0
    daily_vol = returns.std()
    if math.isnan(daily_vol):
        return 0.0

    participation = abs(trade_shares) / adv_shares
    impact_pct = coef * math.sqrt(participation) * daily_vol
    return impact_pct * abs(trade_shares) * price
