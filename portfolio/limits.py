"""Limits shared by Layer 4 sizing and the Layer 5 pre-trade check, read in one place (packet 66)."""


def sector_gross_cap(config: dict) -> float:
    """Max combined long + short gross of one sector, as a fraction of NAV."""
    return float(config.get("risk", {}).get("pre_trade", {}).get("max_sector_pct", 0.25))


def position_cap(config: dict) -> float:
    """Sizing cap: the stricter of Layer 4's and the pre-trade check's max_position_pct."""
    sizing = float(config.get("portfolio", {}).get("max_position_pct", 0.05))
    pre_trade = config.get("risk", {}).get("pre_trade", {})
    return min(sizing, float(pre_trade.get("max_position_pct", 0.05)))
