"""
Analytics Core - The Brain of TradLyte

A pip-installable package containing:
- Technical indicators (RSI, SMA, MACD, etc.) using Polars
- Candle pattern detection (engulfing, hammer, shooting star, etc.)
- Strategy framework (expandable step-based: Setup → Trigger → Exit → ...)
- Pre-built strategy library (Golden Cross, RSI, etc.)
- Composite strategy builder (mix-and-match user strategies)
- Multi-timeframe executor (supports different timeframes per step)
- Backtester engine (position tracking, performance metrics)
- Daily scanner (batch processing for all symbols)
- Data loading utilities (Parquet from S3/Bronze, multi-timeframe support)

Used by:
- Batch Layer: Daily Scanner (all symbols × all strategies)
- Serving Layer: Backtester (1 symbol × 1 custom strategy)
"""

__version__ = "0.1.0"

from .strategies.base import BaseStrategy
from .strategies.builder import CompositeStrategy
from .inputs import load_ohlcv_from_s3, load_ohlcv_from_rds, load_ohlcv_by_timeframe
from .models import (
    StrategyConfig, SetupConfig, TriggerConfig, ExitConfig,
    RequirementsStrategyConfig, ExpandableStrategyConfig, StepConfig,
    SignalResult
)
from .executor import MultiTimeframeExecutor
from .backtester import Backtester, BacktestResult, Position
from .scanner import DailyScanner

__all__ = [
    # Core classes
    'BaseStrategy',
    'CompositeStrategy',
    'MultiTimeframeExecutor',
    'Backtester',
    'BacktestResult',
    'Position',
    'DailyScanner',
    # Data loading
    'load_ohlcv_from_s3',
    'load_ohlcv_from_rds',
    'load_ohlcv_by_timeframe',
    # Models (legacy)
    'StrategyConfig',
    'SetupConfig',
    'TriggerConfig',
    'ExitConfig',
    # Models (new)
    'RequirementsStrategyConfig',
    'ExpandableStrategyConfig',
    'StepConfig',
    'SignalResult',
]
