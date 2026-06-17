"""Stress testing: historical and synthetic portfolio shock scenarios."""
import logging
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import sqlalchemy as sa

from data.db import daily_prices
from factors.db import factor_scores as factor_scores_table

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Historical scenario definitions
# ---------------------------------------------------------------------------

_HISTORICAL = {
    "financial_crisis_2008": ("2008-09-01", "2009-03-31"),
    "covid_crash_2020":      ("2020-02-01", "2020-04-30"),
    "rate_hike_2022":        ("2022-01-01", "2022-10-31"),
}

_SYNTHETIC = ["sector_shock", "momentum_reversal", "short_squeeze"]

_ALL_SCENARIOS = list(_HISTORICAL.keys()) + _SYNTHETIC

_CACHE_MAX_AGE_DAYS = 30


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ScenarioResult:
    name: str
    period: str          # date range string, or "ERROR: <msg>" on failure
    total_pnl_usd: float
    total_pnl_pct: float
    long_pnl_usd: float
    short_pnl_usd: float
    worst_long: str      # ticker with most negative long P&L contribution ("" if none)
    worst_short: str     # ticker with most negative short P&L contribution ("" if none)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_stress_tests(
    conn: sa.engine.Connection,
    positions_df: pd.DataFrame,
    score_date: str,
    config: dict,
    cache_dir: Path,
    scenarios: list[str] | None = None,
) -> list[ScenarioResult]:
    """Run stress scenarios and return a list of ScenarioResult objects.

    Parameters
    ----------
    conn:
        Active SQLAlchemy connection.
    positions_df:
        Current portfolio positions.  Must have columns: ticker, direction,
        weight.  direction is 'LONG' or 'SHORT'; weight is signed (LONG
        positive, SHORT negative).
    score_date:
        ISO date string (YYYY-MM-DD) used as the factor score date.
    config:
        Full config dict; config["portfolio"]["nav_usd"] is required.
    cache_dir:
        Root cache directory.  Parquet files go in cache_dir/stress/.
    scenarios:
        Subset of scenario names to run, or None to run all six.

    Returns
    -------
    List of ScenarioResult, historical scenarios first then synthetic.
    """
    if scenarios is None:
        scenarios = list(_ALL_SCENARIOS)

    nav_usd = float(config.get("portfolio", {}).get("nav_usd", 10_000_000))

    # Ensure cache directory exists
    stress_cache_dir = cache_dir / "stress"
    stress_cache_dir.mkdir(parents=True, exist_ok=True)

    # Load factor scores once — needed for sector imputation and momentum reversal
    factor_scores_df = _load_factor_scores(conn, list(positions_df["ticker"]) if not positions_df.empty else [], score_date)

    results: list[ScenarioResult] = []

    # Run historical scenarios first, then synthetic, in defined order
    historical_names = [s for s in _HISTORICAL if s in scenarios]
    synthetic_names  = [s for s in _SYNTHETIC   if s in scenarios]

    for name in historical_names:
        start, end = _HISTORICAL[name]
        try:
            result = _run_historical(
                positions_df=positions_df,
                scenario_name=name,
                start=start,
                end=end,
                cache_dir=stress_cache_dir,
                factor_scores_df=factor_scores_df,
                nav_usd=nav_usd,
            )
        except Exception as exc:
            logger.warning("stress_test: scenario '%s' failed: %s", name, exc)
            result = ScenarioResult(
                name=name,
                period=f"ERROR: {exc}",
                total_pnl_usd=0.0,
                total_pnl_pct=0.0,
                long_pnl_usd=0.0,
                short_pnl_usd=0.0,
                worst_long="",
                worst_short="",
            )
        results.append(result)

    for name in synthetic_names:
        try:
            result = _run_synthetic(
                positions_df=positions_df,
                scenario_name=name,
                factor_scores_df=factor_scores_df,
                nav_usd=nav_usd,
                score_date=score_date,
            )
        except Exception as exc:
            logger.warning("stress_test: scenario '%s' failed: %s", name, exc)
            result = ScenarioResult(
                name=name,
                period=f"ERROR: {exc}",
                total_pnl_usd=0.0,
                total_pnl_pct=0.0,
                long_pnl_usd=0.0,
                short_pnl_usd=0.0,
                worst_long="",
                worst_short="",
            )
        results.append(result)

    return results


# ---------------------------------------------------------------------------
# Helper: load factor scores
# ---------------------------------------------------------------------------

def _load_factor_scores(
    conn: sa.engine.Connection,
    tickers: list[str],
    score_date: str,
) -> pd.DataFrame:
    """Load momentum_score and sector from factor_scores for the given tickers and date.

    Returns a DataFrame with columns: ticker, sector, momentum_score.
    Missing tickers are silently absent.
    """
    if not tickers:
        return pd.DataFrame(columns=["ticker", "sector", "momentum_score"])

    try:
        rows = conn.execute(
            sa.select(
                factor_scores_table.c.ticker,
                factor_scores_table.c.sector,
                factor_scores_table.c.momentum_score,
            ).where(
                (factor_scores_table.c.score_date == score_date) &
                (factor_scores_table.c.ticker.in_(tickers))
            )
        ).fetchall()

        if rows:
            return pd.DataFrame(rows, columns=["ticker", "sector", "momentum_score"])
    except Exception:
        logger.exception("stress_test: could not load factor scores for score_date=%s", score_date)

    return pd.DataFrame(columns=["ticker", "sector", "momentum_score"])


# ---------------------------------------------------------------------------
# Helper: sector-average return imputation
# ---------------------------------------------------------------------------

def _sector_avg_return(
    ticker_returns: dict[str, float],
    sectors: dict[str, str],
    tickers: list[str],
) -> dict[str, float]:
    """Return imputed returns for tickers that are missing from ticker_returns.

    For each missing ticker, look up its sector and use the mean return of
    same-sector tickers that do have data.  If no sector peers are available,
    fall back to -0.30 (conservative crisis-style default).

    Parameters
    ----------
    ticker_returns:
        Already-computed returns keyed by ticker.
    sectors:
        Sector string keyed by ticker (from factor_scores_df).
    tickers:
        Full list of tickers that need a return.

    Returns
    -------
    A dict mapping *all* tickers in `tickers` to a return float.  Tickers
    already present in ticker_returns are passed through unchanged.
    """
    # Pre-compute sector → mean return from known returns
    sector_returns: dict[str, list[float]] = {}
    for ticker, ret in ticker_returns.items():
        sector = sectors.get(ticker)
        if sector:
            sector_returns.setdefault(sector, []).append(ret)

    sector_mean: dict[str, float] = {
        sec: float(np.mean(vals)) for sec, vals in sector_returns.items()
    }

    result: dict[str, float] = dict(ticker_returns)  # copy known values
    for ticker in tickers:
        if ticker in result:
            continue
        sector = sectors.get(ticker)
        if sector and sector in sector_mean:
            result[ticker] = sector_mean[sector]
            logger.debug(
                "stress_test: imputed return for %s from sector '%s' (%.3f)",
                ticker, sector, sector_mean[sector],
            )
        else:
            result[ticker] = -0.30
            logger.debug(
                "stress_test: imputed return for %s with default -0.30 (no sector peers)",
                ticker,
            )

    return result


# ---------------------------------------------------------------------------
# P&L calculation helper
# ---------------------------------------------------------------------------

def _compute_pnl(
    positions_df: pd.DataFrame,
    returns: dict[str, float],
    nav_usd: float,
) -> ScenarioResult:
    """Shared P&L calculation used by both historical and synthetic runners.

    This function does not populate name/period — caller must set those.

    positions_df columns used: ticker, direction, weight.
    weight is signed (LONG positive, SHORT negative).
    """
    long_pnl_by_ticker: dict[str, float] = {}
    short_pnl_by_ticker: dict[str, float] = {}

    for _, row in positions_df.iterrows():
        ticker    = str(row["ticker"])
        direction = str(row.get("direction", "LONG")).upper()
        weight    = float(row.get("weight", 0.0))
        ret       = returns.get(ticker, 0.0)

        pnl = weight * ret * nav_usd

        if direction == "LONG":
            long_pnl_by_ticker[ticker] = pnl
        else:
            short_pnl_by_ticker[ticker] = pnl

    long_pnl_usd  = float(sum(long_pnl_by_ticker.values()))
    short_pnl_usd = float(sum(short_pnl_by_ticker.values()))
    total_pnl_usd = long_pnl_usd + short_pnl_usd
    total_pnl_pct = total_pnl_usd / nav_usd if nav_usd else 0.0

    # Worst long: ticker with most negative P&L contribution among LONG positions
    worst_long = ""
    if long_pnl_by_ticker:
        candidate = min(long_pnl_by_ticker, key=lambda t: long_pnl_by_ticker[t])
        if long_pnl_by_ticker[candidate] < 0:
            worst_long = candidate

    # Worst short: ticker with most negative P&L contribution among SHORT positions
    # For shorts, weight is negative and a positive return produces a negative P&L —
    # that's what we want to surface here (negative pnl = short squeezed).
    worst_short = ""
    if short_pnl_by_ticker:
        candidate = min(short_pnl_by_ticker, key=lambda t: short_pnl_by_ticker[t])
        if short_pnl_by_ticker[candidate] < 0:
            worst_short = candidate

    return ScenarioResult(
        name="",
        period="",
        total_pnl_usd=round(total_pnl_usd, 2),
        total_pnl_pct=round(total_pnl_pct, 6),
        long_pnl_usd=round(long_pnl_usd, 2),
        short_pnl_usd=round(short_pnl_usd, 2),
        worst_long=worst_long,
        worst_short=worst_short,
    )


# ---------------------------------------------------------------------------
# Historical scenario runner
# ---------------------------------------------------------------------------

def _run_historical(
    positions_df: pd.DataFrame,
    scenario_name: str,
    start: str,
    end: str,
    cache_dir: Path,
    factor_scores_df: pd.DataFrame,
    nav_usd: float,
) -> ScenarioResult:
    """Compute portfolio P&L for a historical stress period.

    1. Try to load a cached parquet (valid if < 30 days old).
    2. On cache miss, download via yfinance and save.
    3. Compute cumulative return per ticker; impute missing via sector average.
    4. Compute P&L.
    """
    if positions_df.empty:
        return ScenarioResult(
            name=scenario_name,
            period=f"{start} to {end}",
            total_pnl_usd=0.0,
            total_pnl_pct=0.0,
            long_pnl_usd=0.0,
            short_pnl_usd=0.0,
            worst_long="",
            worst_short="",
        )

    tickers = list(positions_df["ticker"].unique())

    # ------------------------------------------------------------------
    # Load or refresh price data
    # ------------------------------------------------------------------
    cache_path = cache_dir / f"{scenario_name}.parquet"
    prices_df  = _load_or_fetch_prices(tickers, start, end, cache_path)

    # ------------------------------------------------------------------
    # Compute cumulative returns
    # ------------------------------------------------------------------
    sectors = {}
    if not factor_scores_df.empty:
        for _, r in factor_scores_df.iterrows():
            if pd.notna(r.get("sector")):
                sectors[str(r["ticker"])] = str(r["sector"])

    ticker_returns: dict[str, float] = {}

    for ticker in tickers:
        if prices_df is not None and ticker in prices_df.columns:
            series = prices_df[ticker].dropna()
            if len(series) >= 2:
                first = float(series.iloc[0])
                last  = float(series.iloc[-1])
                if first != 0.0:
                    ticker_returns[ticker] = last / first - 1.0
                    continue
        # Ticker not in data — will be imputed below

    # Impute missing tickers via sector average
    ticker_returns = _sector_avg_return(ticker_returns, sectors, tickers)

    # ------------------------------------------------------------------
    # Compute P&L
    # ------------------------------------------------------------------
    result = _compute_pnl(positions_df, ticker_returns, nav_usd)
    result.name   = scenario_name
    result.period = f"{start} to {end}"
    return result


def _load_or_fetch_prices(
    tickers: list[str],
    start: str,
    end: str,
    cache_path: Path,
) -> pd.DataFrame | None:
    """Return a DataFrame of adjusted close prices indexed by date.

    Columns are ticker symbols.  Returns None on total failure.
    """
    # Check cache validity
    if cache_path.exists():
        age_days = (datetime.now().timestamp() - cache_path.stat().st_mtime) / 86400
        if age_days < _CACHE_MAX_AGE_DAYS:
            try:
                df = pd.read_parquet(cache_path)
                logger.debug("stress_test: loaded cache %s (%d rows)", cache_path.name, len(df))
                return df
            except Exception:
                logger.warning("stress_test: could not read cache %s — re-fetching", cache_path)

    # Fetch from yfinance
    try:
        import yfinance as yf  # noqa: PLC0415

        raw = yf.download(
            tickers,
            start=start,
            end=end,
            auto_adjust=True,
            progress=False,
        )["Close"]

        if raw is None or (hasattr(raw, "empty") and raw.empty):
            logger.warning("stress_test: yfinance returned empty data for %s–%s", start, end)
            return None

        # Normalise to DataFrame
        if isinstance(raw, pd.Series):
            raw = raw.to_frame(name=tickers[0] if len(tickers) == 1 else "unknown")

        try:
            raw.to_parquet(cache_path)
            logger.debug("stress_test: cached %s", cache_path.name)
        except Exception:
            logger.warning("stress_test: could not write cache %s", cache_path)

        return raw

    except Exception:
        logger.exception("stress_test: yfinance download failed for scenario cache %s", cache_path.name)
        return None


# ---------------------------------------------------------------------------
# Synthetic scenario runner
# ---------------------------------------------------------------------------

def _run_synthetic(
    positions_df: pd.DataFrame,
    scenario_name: str,
    factor_scores_df: pd.DataFrame,
    nav_usd: float,
    score_date: str,
) -> ScenarioResult:
    """Compute portfolio P&L for a synthetic stress scenario."""
    if positions_df.empty:
        return ScenarioResult(
            name=scenario_name,
            period="synthetic",
            total_pnl_usd=0.0,
            total_pnl_pct=0.0,
            long_pnl_usd=0.0,
            short_pnl_usd=0.0,
            worst_long="",
            worst_short="",
        )

    if scenario_name == "sector_shock":
        returns = _scenario_sector_shock(positions_df, factor_scores_df)
        period  = "synthetic: sector_shock (-30% top-gross sector)"

    elif scenario_name == "momentum_reversal":
        returns = _scenario_momentum_reversal(positions_df, factor_scores_df)
        period  = "synthetic: momentum_reversal (top/bot quintile ±20%)"

    elif scenario_name == "short_squeeze":
        returns = _scenario_short_squeeze(positions_df)
        period  = "synthetic: short_squeeze (+30% all shorts)"

    else:
        raise ValueError(f"Unknown synthetic scenario: {scenario_name!r}")

    result = _compute_pnl(positions_df, returns, nav_usd)
    result.name   = scenario_name
    result.period = period
    return result


def _scenario_sector_shock(
    positions_df: pd.DataFrame,
    factor_scores_df: pd.DataFrame,
) -> dict[str, float]:
    """Apply -30% return to all tickers in the sector with highest gross exposure."""
    # Build ticker → sector map
    sector_map: dict[str, str] = {}
    if not factor_scores_df.empty:
        for _, r in factor_scores_df.iterrows():
            if pd.notna(r.get("sector")):
                sector_map[str(r["ticker"])] = str(r["sector"])

    # Also use positions_df sector column if available
    if "sector" in positions_df.columns:
        for _, r in positions_df.iterrows():
            t = str(r["ticker"])
            if t not in sector_map and pd.notna(r.get("sector")):
                sector_map[t] = str(r["sector"])

    # Gross exposure by sector (sum of abs weight)
    sector_gross: dict[str, float] = {}
    for _, row in positions_df.iterrows():
        ticker = str(row["ticker"])
        weight = float(row.get("weight", 0.0))
        sector = sector_map.get(ticker, "Unknown")
        sector_gross[sector] = sector_gross.get(sector, 0.0) + abs(weight)

    if not sector_gross:
        # No sector data — zero returns
        return {str(r["ticker"]): 0.0 for _, r in positions_df.iterrows()}

    shocked_sector = max(sector_gross, key=lambda s: sector_gross[s])
    logger.debug(
        "stress_test: sector_shock targeting '%s' (gross=%.3f)",
        shocked_sector, sector_gross[shocked_sector],
    )

    returns: dict[str, float] = {}
    for _, row in positions_df.iterrows():
        ticker = str(row["ticker"])
        sector = sector_map.get(ticker, "Unknown")
        returns[ticker] = -0.30 if sector == shocked_sector else 0.0

    return returns


def _scenario_momentum_reversal(
    positions_df: pd.DataFrame,
    factor_scores_df: pd.DataFrame,
) -> dict[str, float]:
    """Top momentum quintile -20%, bottom quintile +20%, others 0%."""
    # Build ticker → momentum_score map
    mom_map: dict[str, float] = {}
    if not factor_scores_df.empty:
        for _, r in factor_scores_df.iterrows():
            score = r.get("momentum_score")
            if pd.notna(score):
                mom_map[str(r["ticker"])] = float(score)

    tickers = [str(r["ticker"]) for _, r in positions_df.iterrows()]

    # Get scores only for tickers we hold
    held_scores = {t: mom_map[t] for t in tickers if t in mom_map}

    returns: dict[str, float] = {}

    if not held_scores:
        # No momentum data — zero returns
        return {t: 0.0 for t in tickers}

    scores_arr = np.array(list(held_scores.values()))
    p80 = float(np.percentile(scores_arr, 80))
    p20 = float(np.percentile(scores_arr, 20))

    for ticker in tickers:
        score = held_scores.get(ticker)
        if score is None:
            returns[ticker] = 0.0
        elif score >= p80:
            returns[ticker] = -0.20
        elif score <= p20:
            returns[ticker] = +0.20
        else:
            returns[ticker] = 0.0

    return returns


def _scenario_short_squeeze(
    positions_df: pd.DataFrame,
) -> dict[str, float]:
    """Apply +30% return to all SHORT positions; 0% to LONG positions."""
    returns: dict[str, float] = {}
    for _, row in positions_df.iterrows():
        ticker    = str(row["ticker"])
        direction = str(row.get("direction", "LONG")).upper()
        returns[ticker] = +0.30 if direction == "SHORT" else 0.0
    return returns
