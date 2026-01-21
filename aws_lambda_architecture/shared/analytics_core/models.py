"""
Pydantic Models for Strategy Configuration

Validates JSON strategy configurations from users/API
"""

from typing import Literal, Optional, Dict, Any, List
from pydantic import BaseModel, Field, validator


class SetupConfig(BaseModel):
    """Setup (Momentum) Configuration - Step 1: Is the trend valid?"""
    type: Literal[
        'RSI_MOMENTUM',
        'SMA_TREND',
        'MACD_TREND',
        'VOLUME_TREND',
        'NONE'
    ] = Field(..., description="Type of setup/momentum filter")
    
    # RSI Momentum
    min_rsi: Optional[float] = Field(None, ge=0, le=100, description="Minimum RSI value")
    max_rsi: Optional[float] = Field(None, ge=0, le=100, description="Maximum RSI value")
    
    # SMA Trend
    fast_period: Optional[int] = Field(None, gt=0, description="Fast SMA period")
    slow_period: Optional[int] = Field(None, gt=0, description="Slow SMA period")
    direction: Optional[Literal['ABOVE', 'BELOW']] = Field(None, description="Fast above/below slow")
    
    # MACD Trend
    macd_signal: Optional[Literal['BULLISH', 'BEARISH']] = Field(None, description="MACD signal direction")
    
    # Volume Trend
    volume_multiplier: Optional[float] = Field(None, gt=0, description="Volume must be X times average")
    
    @validator('slow_period')
    def slow_greater_than_fast(cls, v, values):
        if v and values.get('fast_period') and v <= values['fast_period']:
            raise ValueError('slow_period must be greater than fast_period')
        return v


class TriggerConfig(BaseModel):
    """Trigger (Pattern) Configuration - Step 2: Did the entry happen?"""
    type: Literal[
        'CANDLE_PATTERN',
        'PRICE_CROSSOVER',
        'INDICATOR_CROSSOVER',
        'BREAKOUT',
        'REVERSAL'
    ] = Field(..., description="Type of trigger/entry signal")
    
    # Candle Pattern
    pattern: Optional[Literal[
        'ENGULFING_BULLISH',
        'ENGULFING_BEARISH',
        'DOJI',
        'HAMMER',
        'SHOOTING_STAR',
        'MORNING_STAR',
        'EVENING_STAR'
    ]] = Field(None, description="Candle pattern type")
    
    # Price Crossover
    price_level: Optional[float] = Field(None, description="Price level to cross")
    direction: Optional[Literal['ABOVE', 'BELOW']] = Field(None, description="Cross above/below")
    
    # Indicator Crossover
    indicator1: Optional[str] = Field(None, description="First indicator name")
    indicator2: Optional[str] = Field(None, description="Second indicator name")
    crossover_type: Optional[Literal['GOLDEN_CROSS', 'DEATH_CROSS']] = Field(None, description="Crossover type")
    
    # Breakout
    breakout_type: Optional[Literal['BOLLINGER_UPPER', 'BOLLINGER_LOWER', 'RESISTANCE', 'SUPPORT']] = Field(None, description="Breakout type")
    confirmation_bars: Optional[int] = Field(None, ge=1, description="Bars to confirm breakout")
    
    # Reversal
    reversal_type: Optional[Literal['RSI_OVERSOLD', 'RSI_OVERBOUGHT', 'STOCH_OVERSOLD', 'STOCH_OVERBOUGHT']] = Field(None, description="Reversal type")


class ExitConfig(BaseModel):
    """Exit (Management) Configuration - Step 3: When do we sell?"""
    type: Literal[
        'STOP_LOSS',
        'TAKE_PROFIT',
        'TRAILING_STOP',
        'TIME_BASED',
        'INDICATOR_SIGNAL',
        'COMBINED'
    ] = Field(..., description="Type of exit/management rule")
    
    # Stop Loss
    stop_loss_pct: Optional[float] = Field(None, ge=0, le=1, description="Stop loss percentage (e.g., 0.05 = 5%)")
    stop_loss_atr_multiplier: Optional[float] = Field(None, gt=0, description="Stop loss as ATR multiplier")
    
    # Take Profit
    take_profit_pct: Optional[float] = Field(None, ge=0, description="Take profit percentage")
    take_profit_atr_multiplier: Optional[float] = Field(None, gt=0, description="Take profit as ATR multiplier")
    
    # Trailing Stop
    trailing_stop_pct: Optional[float] = Field(None, ge=0, le=1, description="Trailing stop percentage")
    trailing_stop_atr_multiplier: Optional[float] = Field(None, gt=0, description="Trailing stop as ATR multiplier")
    
    # Time Based
    max_holding_days: Optional[int] = Field(None, gt=0, description="Maximum holding period in days")
    
    # Indicator Signal
    exit_indicator: Optional[str] = Field(None, description="Indicator name for exit signal")
    exit_condition: Optional[Literal['CROSSOVER', 'THRESHOLD']] = Field(None, description="Exit condition type")
    
    # Combined (multiple exit rules)
    exit_rules: Optional[List[Dict[str, Any]]] = Field(None, description="Multiple exit rules (OR logic)")


class StrategyConfig(BaseModel):
    """Complete Strategy Configuration"""
    name: str = Field(..., description="Strategy name")
    description: Optional[str] = Field(None, description="Strategy description")
    
    setup: SetupConfig = Field(..., description="Setup (momentum) configuration")
    trigger: TriggerConfig = Field(..., description="Trigger (entry) configuration")
    exit: ExitConfig = Field(..., description="Exit (management) configuration")
    
    # Optional filters
    min_market_cap: Optional[float] = Field(None, description="Minimum market cap filter")
    max_market_cap: Optional[float] = Field(None, description="Maximum market cap filter")
    sectors: Optional[List[str]] = Field(None, description="Allowed sectors")
    exclude_sectors: Optional[List[str]] = Field(None, description="Excluded sectors")
    
    # Performance tracking
    enabled: bool = Field(True, description="Whether strategy is enabled")
    priority: int = Field(0, description="Priority for scanning (higher = first)")


class SignalResult(BaseModel):
    """Result from strategy execution"""
    symbol: str
    date: str
    signal: Literal['BUY', 'SELL', 'HOLD']
    price: float
    setup_valid: bool
    trigger_met: bool
    confidence: Optional[float] = Field(None, ge=0, le=1, description="Signal confidence score")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional strategy metadata")
