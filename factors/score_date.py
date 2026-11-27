"""The date a Layer 2 run scores; shared with run_all.py's market-day check (packet 68)."""

from datetime import date


def resolve_score_date(cli_date: str | None, scoring_cfg: dict) -> str:
    """CLI --date, else config scoring.score_date, else today (server local date)."""
    return cli_date or scoring_cfg.get("score_date") or date.today().isoformat()
