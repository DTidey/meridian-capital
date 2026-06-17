"""Provider abstraction — routes to best available data source based on API keys."""

import logging
import os
from enum import Enum

logger = logging.getLogger(__name__)


class PriceProvider(Enum):
    POLYGON = "polygon"
    YFINANCE = "yfinance"


class FundamentalsProvider(Enum):
    FMP = "fmp"
    YFINANCE = "yfinance"


class MacroProvider(Enum):
    FRED = "fred"
    NONE = "none"


class TranscriptProvider(Enum):
    FMP = "fmp"
    NONE = "none"


class Providers:
    """Resolved provider selection based on available API keys."""

    def __init__(self):
        self.polygon_key = os.getenv("POLYGON_API_KEY", "").strip()
        self.fmp_key = os.getenv("FMP_API_KEY", "").strip()
        self.fred_key = os.getenv("FRED_API_KEY", "").strip()

        self.prices = PriceProvider.POLYGON if self.polygon_key else PriceProvider.YFINANCE
        self.fundamentals = FundamentalsProvider.YFINANCE
        self.macro = MacroProvider.FRED if self.fred_key else MacroProvider.NONE
        self.transcripts = TranscriptProvider.FMP if self.fmp_key else TranscriptProvider.NONE

        self._log_selection()

    def _log_selection(self):
        logger.info("Provider selection:")
        if self.prices == PriceProvider.POLYGON:
            logger.info("  Prices      → Polygon (licensed exchange data)")
        else:
            logger.info("  Prices      → yfinance (free)")

        if self.fundamentals == FundamentalsProvider.FMP:
            logger.info("  Fundamentals→ FMP (structured financials)")
        else:
            logger.info("  Fundamentals→ yfinance (free)")

        if self.macro == MacroProvider.FRED:
            logger.info("  Macro       → FRED")
        else:
            logger.info("  Macro       → not configured (no FRED_API_KEY)")

        if self.transcripts == TranscriptProvider.FMP:
            logger.info("  Transcripts → FMP")
        else:
            logger.info("  Transcripts → not configured (no FMP_API_KEY)")

    @property
    def has_polygon(self) -> bool:
        return bool(self.polygon_key)

    @property
    def has_fmp(self) -> bool:
        return bool(self.fmp_key)

    @property
    def has_fred(self) -> bool:
        return bool(self.fred_key)
