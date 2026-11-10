"""Churn replay maths (packet 64): rescoring under other regime weights and a book simulation.

Pure functions over in-memory frames; `portfolio/churn_replay.py` loads the stored history
read-only and prints the results. The book is idealised: equal weight per side, full fills,
no costs, rebuilt each night by `select_book` from the night's candidates and the previous
night's book.
"""

from dataclasses import dataclass, field
from itertools import pairwise

import pandas as pd

from analysis.combined_score import _side_blend
from factors import composite
from factors.regime_weights import adjust_weights, blend_weights, regime_shares, smoothed_vix
from portfolio.book_selection import classify_exits, select_book, side_ranks
from portfolio.book_targets import book_targets

SCORE_COLS = [col for _, col in composite._FACTOR_COLS]


@dataclass
class Scoring:
    """How a night's candidates are scored: stored rows, today's hard switch, or a blend."""

    name: str
    mode: str  # "stored" | "switch" | "blend"
    days: int = 5
    regime_config: dict = field(default_factory=dict)


def with_portfolio(config: dict, **overrides) -> dict:
    """Copy of config with portfolio keys overridden."""
    return {**config, "portfolio": {**config.get("portfolio", {}), **overrides}}


def night_weights(scoring: Scoring, base: dict, stored_regime: str, vix: pd.DataFrame, day: str):
    """(weights, high share, low share) for one night under a recomputed scoring."""
    if scoring.mode == "switch":
        return adjust_weights(base, stored_regime or "NORMAL", scoring.regime_config), None, None
    vix_mean = smoothed_vix(vix, scoring.days, day)
    high, low = regime_shares(vix_mean, scoring.regime_config)
    return blend_weights(base, scoring.regime_config, high, low), high, low


def rescore(day_scores: pd.DataFrame, ai: pd.DataFrame, weights: dict, config: dict):
    """Composite, direction and combined_score for one night, as Layers 2 and 3 compute them.

    `day_scores`: ticker, sector and the eight factor score columns. `ai`: ticker,
    ai_composite, analyzers_used (stored; a new candidate without a row gets the neutral AI
    rank, as Layer 3 does for a name with no analysis).
    """
    factor_results = {
        name: day_scores.set_index("ticker")[[col]] for name, col in composite._FACTOR_COLS
    }
    scored = composite.compute(factor_results, day_scores[["ticker", "sector"]], weights, config)
    df = scored[["sector", "composite_score", "direction"]].rename_axis("ticker").reset_index()
    df = df.merge(ai, on="ticker", how="left")
    df["analyzers_used"] = df["analyzers_used"].fillna(0).astype(int)
    df["combined_score"] = df["composite_score"]
    _band_combined(df, config)
    return df


def _band_combined(df: pd.DataFrame, config: dict) -> None:
    """combined_score on the side bands, as `analysis.combined_score` (in place)."""
    scoring_cfg = config.get("scoring", {})
    combined_cfg = config.get("analysis", {}).get("combined_score", {})
    weights = (
        float(combined_cfg.get("quant_weight", 0.60)),
        float(combined_cfg.get("ai_weight", 0.40)),
    )
    long_t = scoring_cfg.get("long_quintile_threshold", 80)
    short_t = scoring_cfg.get("short_quintile_threshold", 20)
    long_mask, short_mask = df["direction"] == "LONG", df["direction"] == "SHORT"
    blend = _side_blend(df[long_mask], 1, weights)
    df.loc[long_mask, "combined_score"] = long_t + (100 - long_t) * blend / 100
    blend = _side_blend(df[short_mask], -1, weights)
    df.loc[short_mask, "combined_score"] = short_t - short_t * blend / 100


def spearman_pairs(composites: list[pd.Series]) -> list[float]:
    """Day-over-day Spearman of the composite over tickers scored on both days."""
    out = []
    for prev, cur in pairwise(composites):
        both = pd.concat([prev, cur], axis=1, join="inner").dropna()
        out.append(float(both.iloc[:, 0].corr(both.iloc[:, 1], method="spearman")))
    return out


def simulate(nights: list[pd.DataFrame], config: dict, exit_rank: int) -> dict:
    """Rebuild the book each night; count exits, round trips, rank held and turnover."""
    cfg = with_portfolio(config, incumbent_exit_rank=exit_rank)
    pcfg = cfg["portfolio"]
    long_gross, short_gross = book_targets(cfg)
    gross = {"LONG": long_gross, "SHORT": short_gross}
    size = {"LONG": int(pcfg.get("num_longs", 20)), "SHORT": int(pcfg.get("num_shorts", 20))}
    held, entered = {}, {}
    rec = {"exits": [], "turnover": [], "mean_rank": [], "top10_out": []}
    for i, cands in enumerate(nights):
        current = pd.DataFrame(
            {"ticker": list(held), "shares": [1.0 if s == "LONG" else -1.0 for s in held.values()]}
        )
        book = select_book(cands, current, cfg)
        new = dict(zip(book["ticker"], book["direction"], strict=False))
        if i > 0:
            exits = classify_exits(cands, held, book, exit_rank)
            exits["nights_held"] = [i - entered[t] for t in exits["ticker"]]
            rec["exits"].append(exits)
            entries = [t for t, s in new.items() if held.get(t) != s]
            rec["turnover"].append(sum(gross[new[t]] / size[new[t]] for t in entries))
        entered = {t: (entered[t] if held.get(t) == s else i) for t, s in new.items()}
        held = new
        rec["mean_rank"].append(float(book["side_rank"].mean()))
        rec["top10_out"].extend(_top10_left_out(cands, book))
    return summarise(rec)


def _top10_left_out(cands: pd.DataFrame, book: pd.DataFrame) -> list[int]:
    """Per side: candidates at side rank <= 10 that the book does not hold."""
    out = []
    for side in ("LONG", "SHORT"):
        top = set(side_ranks(cands, side).head(10)["ticker"])
        out.append(len(top - set(book.loc[book["direction"] == side, "ticker"])))
    return out


def summarise(rec: dict) -> dict:
    """Totals and means over the replayed nights."""
    exits = pd.concat(rec["exits"], ignore_index=True) if rec["exits"] else pd.DataFrame()
    n = max(len(rec["exits"]), 1)
    reasons = exits["reason"].value_counts() if not exits.empty else pd.Series(dtype=int)
    held = exits["nights_held"] if not exits.empty else pd.Series(dtype=int)
    return {
        "nights": len(rec["exits"]),
        "exits": len(exits),
        "exits_per_night": len(exits) / n,
        "rank_exits": int(reasons.get("beyond_exit_rank", 0) + reasons.get("capped", 0)),
        "band_exits": int(reasons.get("out_of_band", 0)),
        "flip_exits": int(reasons.get("flipped", 0)),
        "round_trips_1": int((held <= 1).sum()),
        "round_trips_2": int((held <= 2).sum()),
        "turnover_per_night": sum(rec["turnover"]) / n,
        "mean_side_rank": sum(rec["mean_rank"]) / max(len(rec["mean_rank"]), 1),
        "top10_left_out": sum(rec["top10_out"]) / max(len(rec["top10_out"]), 1),
    }
