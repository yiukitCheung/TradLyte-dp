"""
Analytics Core - The Brain of TradLyte

A pip-installable package containing:
- Technical indicators (RSI, SMA, MACD, etc.) using Polars
- Strategy framework (3-step: Setup → Trigger → Exit)
- Pre-built strategy library (Golden Cross, RSI, etc.)
- Composite strategy builder (mix-and-match user strategies)
- Data loading utilities (Parquet from S3/Bronze)

Used by:
- Batch Layer: Daily Scanner (all symbols × all strategies)
- Serving Layer: Backtester (1 symbol × 1 custom strategy)
"""

__version__ = "0.1.0"

from .strategies.base import BaseStrategy
from .strategies.builder import CompositeStrategy
from .inputs import load_ohlcv_from_s3, load_ohlcv_from_rds
from .models import StrategyConfig, SetupConfig, TriggerConfig, ExitConfig

__all__ = [
    'BaseStrategy',
    'CompositeStrategy',
    'load_ohlcv_from_s3',
    'load_ohlcv_from_rds',
    'StrategyConfig',
    'SetupConfig',
    'TriggerConfig',
    'ExitConfig',
]
