"""Shortability check with local 7-day cache."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

_CACHE_TTL_S = 7 * 24 * 3600  # 7 days


def is_shortable(ticker: str, client, cache_dir: Path, ttl_days: int = 7) -> bool:
    """
    Return True if the ticker is shortable and easy-to-borrow.
    Result is cached per ticker for *ttl_days* days.
    Returns False on any API error (safe default).
    """
    ttl_s = ttl_days * 24 * 3600
    cache_path = Path(cache_dir) / "shortable" / f"{ticker}.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            if time.time() - cached.get("ts", 0) < ttl_s:
                return bool(cached.get("shortable", False))
        except Exception:
            pass  # corrupt cache — re-fetch

    try:
        asset = client.get_asset(ticker)
        result = bool(
            getattr(asset, "shortable", False) and getattr(asset, "easy_to_borrow", False)
        )
    except Exception as exc:
        log.warning(
            "Shortability check failed for %s: %s — defaulting to not shortable.", ticker, exc
        )
        return False

    cache_path.write_text(json.dumps({"shortable": result, "ts": time.time()}))
    return result
