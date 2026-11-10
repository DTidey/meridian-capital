"""Tests for portfolio/book_selection.py (packet 64, AC1-AC4, AC10)."""

import logging
from pathlib import Path

import pandas as pd
import pytest
import yaml

from portfolio.book_selection import classify_exits, exit_rank, held_sides, select_book


def _cfg(n=5, exit_rank_value=None):
    pcfg = {"num_longs": n, "num_shorts": n}
    if exit_rank_value is not None:
        pcfg["incumbent_exit_rank"] = exit_rank_value
    return {"portfolio": pcfg}


def _cands(n_long=12, n_short=12, extra=()):
    """L01.. LONG at 99, 98, ... (L01 strongest); S01.. SHORT at 1, 2, ... (S01 strongest)."""
    rows = [
        {"ticker": f"L{i:02d}", "direction": "LONG", "combined_score": 100.0 - i}
        for i in range(1, n_long + 1)
    ]
    rows += [
        {"ticker": f"S{i:02d}", "direction": "SHORT", "combined_score": float(i)}
        for i in range(1, n_short + 1)
    ]
    rows += list(extra)
    df = pd.DataFrame(rows)
    df["sector"] = "Tech"
    return df


def _held(*pairs):
    return pd.DataFrame(pairs, columns=["ticker", "shares"])


def _side(book, side):
    return book.loc[book["direction"] == side, "ticker"].tolist()


class TestSelectBook:
    # AC1
    def test_incumbent_within_exit_rank_kept(self):
        book = select_book(_cands(), _held(("L08", 10), ("S07", -10)), _cfg(5, 8))
        assert "L08" in _side(book, "LONG")
        assert "S07" in _side(book, "SHORT")
        row = book.set_index("ticker").loc["L08"]
        assert row["incumbent"] and row["side_rank"] == 8

    def test_fill_with_best_non_held(self):
        book = select_book(_cands(), _held(("L08", 10), ("L07", 5)), _cfg(5, 8))
        assert _side(book, "LONG") == ["L01", "L02", "L03", "L07", "L08"]

    def test_book_size_fixed(self):
        held = _held(*[(f"L{i:02d}", 1) for i in range(4, 10)])
        book = select_book(_cands(), held, _cfg(5, 10))
        assert len(_side(book, "LONG")) == 5
        assert len(_side(book, "SHORT")) == 5

    # AC2
    def test_beyond_exit_rank_closed(self):
        held = {"L09": "LONG"}
        book = select_book(_cands(), _held(("L09", 3)), _cfg(5, 8))
        assert "L09" not in _side(book, "LONG")
        exits = classify_exits(_cands(), held, book, 8)
        assert exits.set_index("ticker").loc["L09", "reason"] == "beyond_exit_rank"

    def test_neutral_incumbent_closed(self):
        cands = _cands(extra=[{"ticker": "N1", "direction": "NEUTRAL", "combined_score": 60.0}])
        cands = cands[cands["direction"] != "NEUTRAL"]  # Layer 4 loads LONG/SHORT only
        book = select_book(cands, _held(("N1", 4)), _cfg(5, 8))
        assert "N1" not in book["ticker"].tolist()
        exits = classify_exits(cands, {"N1": "LONG"}, book, 8)
        assert exits.iloc[0]["reason"] == "out_of_band"

    def test_flipped_incumbent_closed_on_old_side(self):
        cands = _cands(extra=[{"ticker": "X", "direction": "SHORT", "combined_score": 9.5}])
        book = select_book(cands, _held(("X", 4)), _cfg(5, 20))
        assert "X" not in _side(book, "LONG")
        exits = classify_exits(cands, {"X": "LONG"}, book, 20)
        assert exits.iloc[0]["reason"] == "flipped"

    # AC3
    def test_exit_rank_equal_n_matches_top_n(self):
        held = _held(("L06", 1), ("S06", -1))
        book = select_book(_cands(), held, _cfg(5, 5))
        assert _side(book, "LONG") == [f"L{i:02d}" for i in range(1, 6)]
        assert _side(book, "SHORT") == [f"S{i:02d}" for i in range(1, 6)]

    def test_missing_key_matches_top_n(self):
        book = select_book(_cands(), _held(("L06", 1)), _cfg(5))
        assert _side(book, "LONG") == [f"L{i:02d}" for i in range(1, 6)]
        assert not book["incumbent"].any()

    def test_empty_book_top_n(self):
        for current in (None, pd.DataFrame(), _held()):
            book = select_book(_cands(), current, _cfg(5, 10))
            assert _side(book, "LONG") == [f"L{i:02d}" for i in range(1, 6)]
            assert _side(book, "SHORT") == [f"S{i:02d}" for i in range(1, 6)]

    # AC4
    def test_tie_break_by_ticker(self):
        cands = pd.DataFrame(
            {
                "ticker": ["ZZZ", "AAA", "MMM", "BBB"],
                "direction": "LONG",
                "combined_score": [90.0, 90.0, 90.0, 95.0],
            }
        )
        book = select_book(cands, None, _cfg(3, 3))
        assert _side(book, "LONG") == ["BBB", "AAA", "MMM"]

    def test_row_order_independent(self):
        cands = _cands()
        cands.loc[cands["ticker"] == "L03", "combined_score"] = 98.0  # tie with L02
        held = _held(("L07", 1), ("S09", -2), ("L08", 3))
        expected = select_book(cands, held, _cfg(5, 8)).sort_values("ticker")
        for seed in range(5):
            shuffled = cands.sample(frac=1, random_state=seed)
            held_s = held.sample(frac=1, random_state=seed)
            got = select_book(shuffled, held_s, _cfg(5, 8)).sort_values("ticker")
            assert got["ticker"].tolist() == expected["ticker"].tolist()
            assert got["side_rank"].tolist() == expected["side_rank"].tolist()

    # Spec edge cases
    def test_tiny_incumbent_kept(self):
        book = select_book(_cands(), _held(("S08", -4)), _cfg(5, 8))
        assert "S08" in _side(book, "SHORT")

    def test_oversized_side_capped(self):
        held = _held(*[(f"L{i:02d}", 1) for i in range(2, 10)])  # 8 held, num_longs 5
        book = select_book(_cands(), held, _cfg(5, 10))
        assert _side(book, "LONG") == ["L02", "L03", "L04", "L05", "L06"]
        exits = classify_exits(_cands(), held_sides(held), book, 10)
        assert set(exits["reason"]) == {"capped"}

    def test_short_candidate_list(self):
        cands = _cands(n_long=3)
        book = select_book(cands, _held(("L03", 1)), _cfg(5, 8))
        assert _side(book, "LONG") == ["L01", "L02", "L03"]

    def test_missing_score_row_closed(self):
        book = select_book(_cands(), _held(("GONE", 10)), _cfg(5, 8))
        assert "GONE" not in book["ticker"].tolist()
        exits = classify_exits(_cands(), {"GONE": "LONG"}, book, 8)
        assert exits.iloc[0]["reason"] == "out_of_band"

    # Adversarial
    def test_flip_to_top_short(self):
        cands = _cands(extra=[{"ticker": "X", "direction": "SHORT", "combined_score": 0.5}])
        book = select_book(cands, _held(("X", 10)), _cfg(5, 20))
        assert _side(book, "SHORT")[0] == "X"
        assert "X" not in _side(book, "LONG")
        assert (book["ticker"] == "X").sum() == 1
        assert not book.set_index("ticker").loc["X", "incumbent"]  # entered by the top-N rule

    def test_zero_or_missing_shares_not_held(self):
        current = pd.DataFrame({"ticker": ["A", "B", "C", "D"], "shares": [5.0, -2.0, 0.0, None]})
        assert held_sides(current) == {"A": "LONG", "B": "SHORT"}

    def test_held_short_not_incumbent_on_long_side(self):
        """A short position never counts as a long incumbent, even at a buffer rank."""
        book = select_book(_cands(), _held(("L07", -5)), _cfg(5, 8))
        assert "L07" not in _side(book, "LONG")

    def test_selection_logged(self, caplog):
        with caplog.at_level(logging.INFO, logger="portfolio.book_selection"):
            select_book(_cands(), _held(("L07", 1), ("L12", 1)), _cfg(5, 8))
        text = caplog.text
        assert "Book LONG: 5 names, 1 kept by the buffer ['L07']" in text
        assert "beyond_exit_rank" in text

    def test_2026_11_09_fixture(self):
        """11-06 book vs 11-09 side ranks (spec table): buffer keeps APA, closes ATO/DE/VICI."""
        ranks_1109 = {
            ("APA", "LONG"): 21,
            ("WELL", "LONG"): 42,
            ("ATO", "LONG"): 85,
            ("MSI", "SHORT"): 30,
            ("MOS", "SHORT"): 38,
            ("TTWO", "SHORT"): 40,
            ("AJG", "SHORT"): 41,
            ("COHR", "SHORT"): 71,
            ("DE", "SHORT"): 74,
        }
        cands = _fixture_candidates(ranks_1109)
        held = _held(
            *[(t, 1) for t in ("APA", "WELL", "ATO", "VICI")],
            *[(t, -1) for t in ("MSI", "MOS", "TTWO", "AJG", "COHR", "DE", "YUM", "CRL")],
        )
        config = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())
        limit = exit_rank(config)
        book = select_book(cands, held, config)
        selected = set(zip(book["ticker"], book["direction"], strict=True))
        for key, rank in ranks_1109.items():
            assert (key in selected) == (rank <= limit), key
        assert ("APA", "LONG") in selected
        for ticker in ("ATO", "DE", "VICI", "YUM", "CRL"):
            assert ticker not in book["ticker"].tolist()
        assert len(_side(book, "LONG")) == 20 and len(_side(book, "SHORT")) == 20
        exits = classify_exits(cands, held_sides(held), book, limit).set_index("ticker")
        assert exits.loc["VICI", "reason"] == "out_of_band"
        assert exits.loc["DE", "reason"] == "beyond_exit_rank"


def _fixture_candidates(ranks: dict) -> pd.DataFrame:
    """108 LONG and 96 SHORT candidates with the named tickers at the given side ranks."""
    rows = []
    for side, n in (("LONG", 108), ("SHORT", 96)):
        named = {rank: t for (t, s), rank in ranks.items() if s == side}
        for rank in range(1, n + 1):
            ticker = named.get(rank, f"{side[0]}{rank:03d}")
            score = 100.0 - rank * 0.18 if side == "LONG" else rank * 0.2
            rows.append({"ticker": ticker, "direction": side, "combined_score": score})
    df = pd.DataFrame(rows)
    df["sector"] = "Tech"
    return df


# AC10
class TestExitRankConfig:
    def test_exit_rank_below_n_raises(self):
        with pytest.raises(ValueError, match="portfolio.incumbent_exit_rank"):
            exit_rank(_cfg(20, 19))

    @pytest.mark.parametrize("bad", [35.0, "35", True])
    def test_exit_rank_not_integer_raises(self, bad):
        with pytest.raises(ValueError, match="portfolio.incumbent_exit_rank"):
            exit_rank(_cfg(20, bad))

    def test_exit_rank_uses_larger_side(self):
        config = {"portfolio": {"num_longs": 20, "num_shorts": 25, "incumbent_exit_rank": 22}}
        with pytest.raises(ValueError, match="incumbent_exit_rank"):
            exit_rank(config)

    def test_default_is_book_size(self):
        assert exit_rank(_cfg(20)) == 20

    def test_select_book_validates(self):
        with pytest.raises(ValueError, match="incumbent_exit_rank"):
            select_book(_cands(), None, _cfg(5, 4))
