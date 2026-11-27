"""The date a Layer 2 run scores; shared with run_all.py's market-day check (packet 68)."""

from datetime import date


def run_date() -> str:
    """Today's date (server local) as ISO; read once per run_all.py run."""
    return date.today().isoformat()


def resolve_score_date(cli_date: str | None, scoring_cfg: dict, today: str | None = None) -> str:
    """CLI --date, else config scoring.score_date, else today (or the given run date)."""
    return cli_date or scoring_cfg.get("score_date") or today or run_date()
