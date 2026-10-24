"""Conviction-tilt portfolio optimizer — always converges."""

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
import sqlalchemy as sa

from data.db import earnings_calendar
from portfolio.book_targets import book_targets
from portfolio.transaction_costs import compute_adv

logger = logging.getLogger(__name__)

_MAX_BETA_TILT = 20.0  # strongest exp(k * beta) tilt tried
_BISECTION_STEPS = 40


def optimise(
    candidates: pd.DataFrame,
    prices: dict[str, pd.DataFrame],
    betas: pd.Series,
    config: dict,
    score_date: str,
    conn: sa.engine.Connection,
) -> pd.DataFrame:
    """Conviction-tilt optimisation.

    Args:
        candidates: DataFrame with columns [ticker, direction, combined_score, sector].
                    Must contain only LONG and SHORT rows.
        prices: Dict mapping ticker → recent OHLCV DataFrame.
        betas: Series[ticker → beta].
        config: Full application config.
        score_date: ISO date string.
        conn: DB connection (for earnings lookup).

    Returns:
        DataFrame with columns [ticker, direction, weight, shares, sector,
        combined_score, beta, current_price].
    """
    pcfg = config.get("portfolio", {})
    nav = float(pcfg.get("nav_usd", 10_000_000))
    num_longs = int(pcfg.get("num_longs", 20))
    num_shorts = int(pcfg.get("num_shorts", 20))
    long_gross, short_gross = book_targets(config)
    max_pos = float(pcfg.get("max_position_pct", 0.05))
    min_pos = float(pcfg.get("min_position_pct", 0.005))
    max_sector = float(pcfg.get("max_sector_pct", 0.25))
    max_sector_net = float(pcfg.get("max_sector_net_pct", 0.05))
    max_beta = float(pcfg.get("max_beta", 0.15))
    adv_max_pct = float(pcfg.get("adv_max_pct", 0.05))
    adv_days = int(pcfg.get("adv_lookback_days", 20))
    blackout_days = int(pcfg.get("earnings_blackout_days", 5))
    tilt_cfg = pcfg.get("conviction_tilt", {})
    top5_mult = float(tilt_cfg.get("top5_multiplier", 1.50))
    top10_mult = float(tilt_cfg.get("top10_multiplier", 1.25))

    longs = (
        candidates[candidates["direction"] == "LONG"]
        .sort_values("combined_score", ascending=False)
        .head(num_longs)
        .copy()
    )
    shorts = (
        candidates[candidates["direction"] == "SHORT"]
        .sort_values("combined_score", ascending=True)
        .head(num_shorts)
        .copy()
    )

    if longs.empty and shorts.empty:
        logger.warning("Optimizer: no candidates")
        return pd.DataFrame()

    # 1. Equal-weight base
    longs["weight"] = long_gross / max(len(longs), 1)
    shorts["weight"] = short_gross / max(len(shorts), 1)

    # 2. Conviction tilt within each book
    longs = _apply_conviction_tilt(longs, top5_mult, top10_mult, long_gross)
    shorts = _apply_conviction_tilt(shorts, top5_mult, top10_mult, short_gross, ascending=True)

    # 3. Earnings haircut
    blackout = _earnings_blackout_set(
        conn, candidates["ticker"].tolist(), score_date, blackout_days
    )
    longs = _earnings_haircut(longs, blackout, long_gross)
    shorts = _earnings_haircut(shorts, blackout, short_gross)

    # 4. Liquidity cap
    longs = _liquidity_cap(longs, prices, adv_max_pct, adv_days, nav, long_gross)
    shorts = _liquidity_cap(shorts, prices, adv_max_pct, adv_days, nav, short_gross)

    # 5. Position bounds
    longs = _clamp_and_renorm(longs, min_pos, max_pos, long_gross)
    shorts = _clamp_and_renorm(shorts, min_pos, max_pos, short_gross)

    # 6. Sector neutrality
    combined = pd.concat([longs, shorts], ignore_index=True)
    combined = _enforce_sector_neutral(
        combined, max_sector, max_sector_net, long_gross, short_gross
    )
    longs = combined[combined["direction"] == "LONG"].copy()
    shorts = combined[combined["direction"] == "SHORT"].copy()

    # 7. Beta adjustment (reweight within each book to meet the constraint)
    all_tickers = pd.concat([longs, shorts])["ticker"].tolist()
    if betas.empty:
        betas = pd.Series(dict.fromkeys(all_tickers, 1.0))

    combined = pd.concat([longs, shorts], ignore_index=True)
    combined = _adjust_beta(combined, betas, max_beta, long_gross, short_gross, min_pos, max_pos)

    # 8. Compute share counts and current prices
    combined = _add_shares_and_prices(combined, prices, nav)

    # Shorts get negative weight/shares to indicate short position
    mask = combined["direction"] == "SHORT"
    combined.loc[mask, "weight"] *= -1
    combined.loc[mask, "shares"] *= -1

    combined["beta"] = combined["ticker"].map(betas).fillna(1.0)
    return combined[
        [
            "ticker",
            "direction",
            "weight",
            "shares",
            "sector",
            "combined_score",
            "beta",
            "current_price",
        ]
    ].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Step helpers
# ---------------------------------------------------------------------------


def _apply_conviction_tilt(
    book: pd.DataFrame,
    top5_mult: float,
    top10_mult: float,
    gross_target: float,
    ascending: bool = False,
) -> pd.DataFrame:
    """Boost the strongest positions; ascending=True for the short book (low score = strong)."""
    n = len(book)
    if n == 0:
        return book
    df = book.sort_values("combined_score", ascending=ascending).copy()
    top5_cut = max(1, int(np.ceil(n * 0.05)))
    top10_cut = max(1, int(np.ceil(n * 0.10)))

    df["weight"] = gross_target / n
    df.iloc[:top5_cut, df.columns.get_loc("weight")] *= top5_mult
    df.iloc[top5_cut:top10_cut, df.columns.get_loc("weight")] *= top10_mult
    total = df["weight"].sum()
    if total > 0:
        df["weight"] = df["weight"] / total * gross_target
    return df


def _earnings_haircut(
    book: pd.DataFrame,
    blackout: set[str],
    gross_target: float,
) -> pd.DataFrame:
    if book.empty or not blackout:
        return book
    df = book.copy()
    in_blackout = df["ticker"].isin(blackout)
    surplus = df.loc[in_blackout, "weight"].sum() * 0.5
    df.loc[in_blackout, "weight"] *= 0.5
    n_safe = (~in_blackout).sum()
    if n_safe > 0 and surplus > 0:
        df.loc[~in_blackout, "weight"] += surplus / n_safe
    total = df["weight"].sum()
    if total > 0:
        df["weight"] = df["weight"] / total * gross_target
    return df


def _liquidity_cap(
    book: pd.DataFrame,
    prices: dict[str, pd.DataFrame],
    adv_max_pct: float,
    adv_days: int,
    nav: float,
    gross_target: float,
) -> pd.DataFrame:
    if book.empty:
        return book
    df = book.copy()
    changed = True
    while changed:
        changed = False
        for idx, row in df.iterrows():
            ticker = row["ticker"]
            price_df = prices.get(ticker, pd.DataFrame())
            if price_df.empty:
                continue
            adv_usd = compute_adv(price_df, adv_days)
            if adv_usd <= 0:
                continue
            max_weight = adv_max_pct * adv_usd / nav
            if row["weight"] > max_weight:
                surplus = row["weight"] - max_weight
                df.at[idx, "weight"] = max_weight
                others = df.index[df.index != idx]
                if len(others) > 0:
                    df.loc[others, "weight"] += surplus / len(others)
                changed = True
                break
    total = df["weight"].sum()
    if total > 0:
        df["weight"] = df["weight"] / total * gross_target
    return df


def _clamp_and_renorm(
    book: pd.DataFrame,
    min_pos: float,
    max_pos: float,
    gross_target: float,
) -> pd.DataFrame:
    if book.empty:
        return book
    df = book.copy()
    df["weight"] = df["weight"].clip(lower=min_pos, upper=max_pos)
    total = df["weight"].sum()
    if total > 0:
        df["weight"] = df["weight"] / total * gross_target
    return df


def _enforce_sector_neutral(
    combined: pd.DataFrame,
    max_sector: float,
    max_sector_net: float,
    long_gross: float,
    short_gross: float,
) -> pd.DataFrame:
    df = combined.copy()
    sectors = df["sector"].dropna().unique()

    for sector in sectors:
        long_mask = (df["direction"] == "LONG") & (df["sector"] == sector)
        short_mask = (df["direction"] == "SHORT") & (df["sector"] == sector)

        long_sum = df.loc[long_mask, "weight"].sum()
        short_sum = df.loc[short_mask, "weight"].sum()

        # Single-side sector cap
        if long_sum > max_sector and df.loc[long_mask].shape[0] > 0:
            df.loc[long_mask, "weight"] *= max_sector / long_sum
        if short_sum > max_sector and df.loc[short_mask].shape[0] > 0:
            df.loc[short_mask, "weight"] *= max_sector / short_sum

        # Net sector cap
        net = df.loc[long_mask, "weight"].sum() - df.loc[short_mask, "weight"].sum()
        if abs(net) > max_sector_net:
            if net > 0 and df.loc[long_mask].shape[0] > 0:
                scale = (long_sum - (net - max_sector_net)) / long_sum if long_sum > 0 else 1.0
                df.loc[long_mask, "weight"] *= max(0.0, scale)
            elif net < 0 and df.loc[short_mask].shape[0] > 0:
                scale = (
                    (short_sum - (abs(net) - max_sector_net)) / short_sum if short_sum > 0 else 1.0
                )
                df.loc[short_mask, "weight"] *= max(0.0, scale)

    # Re-normalise each book back to its gross target
    for direction, target in [("LONG", long_gross), ("SHORT", short_gross)]:
        mask = df["direction"] == direction
        total = df.loc[mask, "weight"].sum()
        if total > 0:
            df.loc[mask, "weight"] = df.loc[mask, "weight"] / total * target
    return df


def _adjust_beta(
    combined: pd.DataFrame,
    betas: pd.Series,
    max_beta: float,
    long_gross: float,
    short_gross: float,
    min_pos: float,
    max_pos: float,
) -> pd.DataFrame:
    """Reweight within each book until |net beta| <= max_beta (weights positive here).

    Longs are scaled by exp(-k * beta) and shorts by exp(+k * beta), then each book is scaled
    back to its gross target within the position bounds; k is the smallest tilt found by
    bisection. Skipped, with a warning, when a book cannot hold its gross within the bounds.
    """
    beta = combined["ticker"].map(betas).fillna(1.0)
    net = _net_beta(combined, beta)
    if abs(net) <= max_beta:
        return combined
    if not _books_feasible(combined, long_gross, short_gross, min_pos, max_pos):
        logger.warning("Optimizer: beta tilt skipped; a book cannot hold its gross in bounds")
        return combined

    sign = float(np.sign(net))

    def tilt(t: float) -> pd.DataFrame:
        return _tilt_books(combined, beta, sign * t, long_gross, short_gross, min_pos, max_pos)

    low, high = 0.0, _MAX_BETA_TILT
    for _ in range(_BISECTION_STEPS):
        mid = (low + high) / 2
        if sign * _net_beta(tilt(mid), beta) > max_beta:
            low = mid
        else:
            high = mid
    result = tilt(high)
    if abs(_net_beta(result, beta)) > max_beta + 1e-9:
        logger.warning(
            "Optimizer: net beta %.3f cannot reach the %.2f limit by reweighting (best %.3f)",
            net,
            max_beta,
            _net_beta(result, beta),
        )
    return result


def _books_feasible(df, long_gross, short_gross, min_pos, max_pos) -> bool:
    """True when each book can sum to its gross with every weight in [min_pos, max_pos]."""
    for direction, gross in (("LONG", long_gross), ("SHORT", short_gross)):
        n = int((df["direction"] == direction).sum())
        if n and not (n * min_pos - 1e-9 <= gross <= n * max_pos + 1e-9):
            return False
    return True


def _tilt_books(
    df: pd.DataFrame,
    beta: pd.Series,
    k: float,
    long_gross: float,
    short_gross: float,
    min_pos: float,
    max_pos: float,
) -> pd.DataFrame:
    """Tilt by exp(-k * beta) for longs and exp(+k * beta) for shorts; rescale each book."""
    out = df.copy()
    long_mask = out["direction"] == "LONG"
    out.loc[long_mask, "weight"] *= np.exp(-k * beta[long_mask])
    out.loc[~long_mask, "weight"] *= np.exp(k * beta[~long_mask])
    out.loc[long_mask, "weight"] = _bounded_scale(
        out.loc[long_mask, "weight"], min_pos, max_pos, long_gross
    )
    out.loc[~long_mask, "weight"] = _bounded_scale(
        out.loc[~long_mask, "weight"], min_pos, max_pos, short_gross
    )
    return out


def _bounded_scale(w: pd.Series, lo: float, hi: float, gross: float) -> pd.Series:
    """Scale w to sum to gross with every weight in [lo, hi], keeping the order of the rest.

    Weights pushed past a bound are fixed at it and the others share what is left, so a strong
    tilt saturates at the bounds instead of collapsing back to equal weights.
    """
    fixed = pd.Series(np.nan, index=w.index)
    for _ in range(len(w) + 1):
        free = fixed.isna()
        if not free.any():
            break
        total = w[free].sum()
        remaining = gross - fixed.sum()
        if total > 0:
            scaled = w[free] * remaining / total
        else:
            scaled = pd.Series(remaining / free.sum(), index=w[free].index)
        over, under = scaled > hi, scaled < lo
        if not over.any() and not under.any():
            fixed[free] = scaled
            break
        # One side per pass: fixing both at once can overshoot the gross target
        if over.any():
            fixed[scaled.index[over]] = hi
        else:
            fixed[scaled.index[under]] = lo
    return fixed


def _net_beta(df: pd.DataFrame, beta: pd.Series) -> float:
    sign = np.where(df["direction"] == "LONG", 1.0, -1.0)
    return float((df["weight"] * sign * beta.loc[df.index]).sum())


def _add_shares_and_prices(
    combined: pd.DataFrame,
    prices: dict[str, pd.DataFrame],
    nav: float,
) -> pd.DataFrame:
    df = combined.copy()
    df["current_price"] = 0.0
    df["shares"] = 0.0

    for idx, row in df.iterrows():
        ticker = row["ticker"]
        price_df = prices.get(ticker, pd.DataFrame())
        if not price_df.empty:
            close_col = "close" if "close" in price_df.columns else "adj_close"
            price = float(price_df[close_col].iloc[-1]) if close_col in price_df.columns else 0.0
        else:
            price = 0.0
        df.at[idx, "current_price"] = price
        if price > 0:
            position_value = abs(row["weight"]) * nav
            df.at[idx, "shares"] = position_value / price
    return df


def _earnings_blackout_set(
    conn: sa.engine.Connection,
    tickers: list[str],
    score_date: str,
    blackout_days: int,
) -> set[str]:
    if not tickers:
        return set()
    window_end = str(date.fromisoformat(score_date) + timedelta(days=blackout_days))
    rows = conn.execute(
        sa.select(earnings_calendar.c.ticker).where(
            earnings_calendar.c.ticker.in_(tickers)
            & (earnings_calendar.c.earnings_date >= score_date)
            & (earnings_calendar.c.earnings_date <= window_end)
        )
    ).fetchall()
    return {r[0] for r in rows}
