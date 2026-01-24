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

from .momentum_strategies import GoldenCrossStrategy, RSIMomentumStrategy, MACDCrossoverStrategy
from .breakout_strategies import BollingerBreakoutStrategy, ATRBreakoutStrategy
from .vegas_channel_strategy import VegasChannelStrategy

__all__ = [
    'GoldenCrossStrategy',
    'RSIMomentumStrategy',
    'MACDCrossoverStrategy',
    'BollingerBreakoutStrategy',
    'ATRBreakoutStrategy',
    'VegasChannelStrategy',
]
