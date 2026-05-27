"""
Technical Indicators Module

All indicators use Polars for high-performance calculations. Every indicator
emits a parametric column name (e.g. ``rsi_14``, ``macd_12_26``) so that
multiple parameter instances can coexist in the same DataFrame and be
referenced by the JSON expression DSL in ``strategies/builder.py``.
"""

from .technicals import (
    # Indicator calculators
    calculate_rsi,
    calculate_sma,
    calculate_ema,
    calculate_macd,
    calculate_bollinger_bands,
    calculate_atr,
    calculate_stochastic,
    calculate_all_indicators,
    # Column-name helpers
    rsi_col,
    sma_col,
    ema_col,
    atr_col,
    macd_cols,
    bb_cols,
    stoch_cols,
    # Registry API
    INDICATOR_REGISTRY,
    resolve_indicator,
)
from .patterns import detect_all_patterns

__all__ = [
    "calculate_rsi",
    "calculate_sma",
    "calculate_ema",
    "calculate_macd",
    "calculate_bollinger_bands",
    "calculate_atr",
    "calculate_stochastic",
    "calculate_all_indicators",
    "rsi_col",
    "sma_col",
    "ema_col",
    "atr_col",
    "macd_cols",
    "bb_cols",
    "stoch_cols",
    "INDICATOR_REGISTRY",
    "resolve_indicator",
    "detect_all_patterns",
]
