"""
Technical Indicators Module

All indicators use Polars for high-performance calculations
"""

from .technicals import (
    calculate_rsi,
    calculate_sma,
    calculate_ema,
    calculate_macd,
    calculate_bollinger_bands,
    calculate_atr,
    calculate_stochastic,
    calculate_all_indicators,
)
from .patterns import detect_all_patterns

__all__ = [
    'calculate_rsi',
    'calculate_sma',
    'calculate_ema',
    'calculate_macd',
    'calculate_bollinger_bands',
    'calculate_atr',
    'calculate_stochastic',
    'calculate_all_indicators',
    'detect_all_patterns',
]
