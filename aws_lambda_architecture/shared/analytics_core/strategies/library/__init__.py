"""
Pre-built Strategy Library

Common trading strategies ready to use:
- Golden Cross (SMA 50/200)
- RSI Oversold/Overbought
- MACD Crossover
- Bollinger Band Breakout
- etc.
"""

from .momentum_strategies import GoldenCrossStrategy, RSIMomentumStrategy, MACDCrossoverStrategy
from .breakout_strategies import BollingerBreakoutStrategy, ATRBreakoutStrategy

__all__ = [
    'GoldenCrossStrategy',
    'RSIMomentumStrategy',
    'MACDCrossoverStrategy',
    'BollingerBreakoutStrategy',
    'ATRBreakoutStrategy',
]
