"""Layer 4 book membership: top-N entry with an incumbent buffer (packet 64)."""

import logging

import pandas as pd

logger = logging.getLogger(__name__)

_SIDES = (("LONG", "num_longs"), ("SHORT", "num_shorts"))


def exit_rank(config: dict) -> int:
    """`portfolio.incumbent_exit_rank`, validated; defaults to max(num_longs, num_shorts)."""
    pcfg = config.get("portfolio", {})
    book_max = max(int(pcfg.get("num_longs", 20)), int(pcfg.get("num_shorts", 20)))
    value = pcfg.get("incumbent_exit_rank", book_max)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"portfolio.incumbent_exit_rank must be an integer, got {value!r}")
    if value < book_max:
        raise ValueError(
            f"portfolio.incumbent_exit_rank ({value}) must be >= max(num_longs, num_shorts) "
            f"({book_max})"
        )
    return value


def held_sides(current: pd.DataFrame | None) -> dict[str, str]:
    """Ticker -> LONG/SHORT from broker positions: positive shares long, negative short."""
    if current is None or current.empty or "shares" not in current.columns:
        return {}
    shares = current.set_index("ticker")["shares"].astype(float)
    held = {t: "LONG" for t, s in shares.items() if s > 0}
    return held | {t: "SHORT" for t, s in shares.items() if s < 0}  # zero or NaN: not held


def side_ranks(candidates: pd.DataFrame, side: str) -> pd.DataFrame:
    """The side's candidates with a 1-based `side_rank`; ties break by ticker ascending."""
    strongest_low = side == "SHORT"
    side_df = candidates[candidates["direction"] == side]
    ordered = side_df.sort_values(
        ["combined_score", "ticker"], ascending=[strongest_low, True], kind="mergesort"
    ).copy()
    ordered["side_rank"] = range(1, len(ordered) + 1)
    return ordered


def select_book(
    candidates: pd.DataFrame, current: pd.DataFrame | None, config: dict
) -> pd.DataFrame:
    """Selected LONG then SHORT rows with `side_rank` and `incumbent`, best rank first.

    Per side: held names that are candidates on the same side with side rank <= the exit
    rank are kept (best rank first, capped at num_*), then the best ranked remaining
    candidates fill the free slots.
    """
    limit = exit_rank(config)
    held = held_sides(current)
    pcfg = config.get("portfolio", {})
    books = []
    for side, size_key in _SIDES:
        size = int(pcfg.get(size_key, 20))
        ranked = side_ranks(candidates, side)
        is_held = ranked["ticker"].map(held).eq(side)
        incumbents = ranked[is_held & (ranked["side_rank"] <= limit)].head(size)
        fill = ranked[~ranked.index.isin(incumbents.index)].head(size - len(incumbents))
        book = pd.concat([incumbents, fill]).sort_values("side_rank")
        book["incumbent"] = book.index.isin(incumbents.index)
        books.append(book)
    selected = pd.concat(books, ignore_index=True)
    _log_selection(selected, classify_exits(candidates, held, selected, limit), pcfg)
    return selected


def classify_exits(
    candidates: pd.DataFrame, held: dict[str, str], selected: pd.DataFrame, limit: int
) -> pd.DataFrame:
    """Held names not selected on their side, with the reason they close.

    Reasons: `out_of_band` (no LONG/SHORT row), `flipped` (candidate on the other side),
    `beyond_exit_rank`, `capped` (within the exit rank but the side already holds num_*).
    """
    kept = set(zip(selected["ticker"], selected["direction"], strict=False))
    ranks = {}
    for side, _ in _SIDES:
        ranked = side_ranks(candidates, side)
        ranks.update(
            {(t, side): r for t, r in zip(ranked["ticker"], ranked["side_rank"], strict=True)}
        )
    now_side = dict(zip(candidates["ticker"], candidates["direction"], strict=False))
    rows = []
    for ticker, side in sorted(held.items()):
        if (ticker, side) in kept:
            continue
        rank = ranks.get((ticker, side))
        if now_side.get(ticker) not in ("LONG", "SHORT"):
            reason = "out_of_band"
        elif rank is None:
            reason = "flipped"
        else:
            reason = "beyond_exit_rank" if rank > limit else "capped"
        rows.append({"ticker": ticker, "side": side, "side_rank": rank, "reason": reason})
    return pd.DataFrame(rows, columns=["ticker", "side", "side_rank", "reason"])


def _log_selection(selected: pd.DataFrame, exits: pd.DataFrame, pcfg: dict) -> None:
    """Per side: names kept by the buffer and held names closed, by reason."""
    for side, size_key in _SIDES:
        size = int(pcfg.get(size_key, 20))
        book = selected[selected["direction"] == side]
        buffered = book[book["incumbent"] & (book["side_rank"] > size)]
        closed = exits[exits["side"] == side]["reason"].value_counts().to_dict()
        logger.info(
            "Book %s: %d names, %d kept by the buffer %s; closed %s",
            side,
            len(book),
            len(buffered),
            sorted(buffered["ticker"]),
            closed or "none",
        )
