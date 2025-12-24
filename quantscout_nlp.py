"""
quantscout_nlp.py
Institutional-Grade Configuration & Constants
QuantScout NLP Sentiment Tracker – December 2025 Expansion
"""

from __future__ import annotations

from typing import List, Final

# ==================== INSTITUTIONAL TICKER UNIVERSE (15 Symbols) ====================
TICKER_UNIVERSE: Final[List[str]] = [
    "TSLA",   # EV / AI leader
    "SNOW",   # Cloud data warehousing
    "DUOL",   # EdTech growth
    "ORCL",   # Enterprise cloud transition
    "RDDT",   # Social media / advertising
    "SHOP",   # E-commerce platform
    "MU",     # Memory semiconductors
    "DASH",   # Food delivery / logistics
    "ARM",    # Chip architecture
    "RKLB",   # Space launch
    "LEU",    # Uranium enrichment (nuclear tailwind)
    "OKLO",   # Small modular reactors
    "PLTR",   # Data analytics / government contracts
    "NVDA",   # AI GPU dominance
    "CRWD",   # Cybersecurity leader
]

assert len(TICKER_UNIVERSE) == 15, "Ticker universe must contain exactly 15 symbols"

# ==================== RISK & STRATEGY PARAMETERS ====================
MAX_POSITION_PCT_EQUITY: Final[float] = 0.12   # Max 12% of equity per name
MAX_TOTAL_LONG_EXPOSURE: Final[float] = 1.00   # Long-only max 100%
CONFIDENCE_THRESHOLD_BUY: Final[float] = 0.65
CONFIDENCE_THRESHOLD_SELL: Final[float] = 0.70  # Slightly higher bar to exit
MIN_HEADLINES_REQUIRED: Final[int] = 5         # Avoid thin news days

# ==================== SENTIMENT MAPPING ====================
SENTIMENT_LABEL_MAP: Final[dict[str, str]] = {
    "positive": "positive",
    "negative": "negative",
    "neutral": "neutral",
}

# ==================== DEFENSIVE ASSERTIONS ====================
def validate_config() -> None:
    """Runtime validation – fails fast on misconfiguration"""
    assert 0.05 <= MAX_POSITION_PCT_EQUITY <= 0.20, "Position cap out of prudent range"
    assert 0.50 <= CONFIDENCE_THRESHOLD_BUY <= 0.80, "Buy threshold unreasonable"
    assert CONFIDENCE_THRESHOLD_SELL >= CONFIDENCE_THRESHOLD_BUY, "Sell threshold must be >= buy"

# Run validation on import
validate_config()

__all__ = [
    "TICKER_UNIVERSE",
    "MAX_POSITION_PCT_EQUITY",
    "MAX_TOTAL_LONG_EXPOSURE",
    "CONFIDENCE_THRESHOLD_BUY",
    "CONFIDENCE_THRESHOLD_SELL",
    "MIN_HEADLINES_REQUIRED",
    "SENTIMENT_LABEL_MAP",
]
