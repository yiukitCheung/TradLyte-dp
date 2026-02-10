"""
Pre-built Strategy Library

Common trading strategies ready to use:
- Golden Cross (SMA 50/200)
- RSI Oversold/Overbought
- MACD Crossover
- Bollinger Band Breakout
- Vegas Channel (Multi-timeframe EMA trend following)
- etc.
"""

from .golden_cross_strategy import GoldenCrossStrategy
from .vegas_channel_strategy import VegasChannelStrategy

__all__ = [
    'GoldenCrossStrategy',
    'VegasChannelStrategy',
]
