"""
Pydantic Models for Strategy Configuration

Validates JSON strategy configurations from users/API
Supports both legacy 3-step format and new expandable step-based format
"""

from typing import Literal, Optional, Dict, Any, List, Union
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


# ============================================================================
# NEW: Expandable Step-Based Architecture (Requirements JSON Format)
# ============================================================================

class StepConfig(BaseModel):
    """Base class for expandable strategy steps"""
    step_name: str = Field(..., description="Name of the step (e.g., 'setup', 'trigger', 'exit')")
    timeframe: str = Field("1d", description="Candle interval for this step (e.g., '1d', '3d', '5d', '1h')")
    enabled: bool = Field(True, description="Whether this step is enabled")


class IndicatorThresholdConfig(BaseModel):
    """Indicator threshold configuration (for requirements JSON format)"""
    type: Literal['INDICATOR_THRESHOLD'] = 'INDICATOR_THRESHOLD'
    indicator: str = Field(..., description="Indicator name (e.g., 'RSI', 'SMA', 'EMA', 'MACD')")
    params: Dict[str, Any] = Field(default_factory=dict, description="Indicator parameters (e.g., {'period': 14})")
    operator: Literal['>', '<', '>=', '<=', '==', 'CROSS_ABOVE', 'CROSS_BELOW'] = Field(..., description="Comparison operator")
    value: Optional[float] = Field(None, description="Threshold value (not required for CROSS_ABOVE/BELOW)")
    indicator2: Optional[str] = Field(None, description="Second indicator for crossover (required for CROSS_ABOVE/BELOW)")


class CandlePatternConfig(BaseModel):
    """Candle pattern configuration (for requirements JSON format)"""
    type: Literal['CANDLE_PATTERN'] = 'CANDLE_PATTERN'
    pattern: Literal[
        'BULLISH_ENGULFING',
        'BEARISH_ENGULFING',
        'ENGULFING_BULLISH',  # Alias for backward compatibility
        'ENGULFING_BEARISH',  # Alias for backward compatibility
        'HAMMER',
        'SHOOTING_STAR',
        'DOJI',
        'MORNING_STAR',
        'EVENING_STAR',
        'GREEN_CANDLE',  # Simple bullish candle
        'RED_CANDLE'     # Simple bearish candle
    ] = Field(..., description="Candle pattern type")


class PriceCrossoverConfig(BaseModel):
    """Price crossover configuration"""
    type: Literal['PRICE_CROSSOVER'] = 'PRICE_CROSSOVER'
    price_level: Optional[float] = Field(None, description="Price level to cross")
    indicator: Optional[str] = Field(None, description="Indicator to cross (e.g., 'SMA_50')")
    direction: Literal['ABOVE', 'BELOW'] = Field(..., description="Cross above/below")


class IndicatorCrossoverConfig(BaseModel):
    """Indicator crossover configuration"""
    type: Literal['INDICATOR_CROSSOVER'] = 'INDICATOR_CROSSOVER'
    indicator1: str = Field(..., description="First indicator name")
    indicator2: str = Field(..., description="Second indicator name")
    crossover_type: Literal['GOLDEN_CROSS', 'DEATH_CROSS'] = Field(..., description="Crossover type")


class StopLossConfig(BaseModel):
    """Stop loss configuration"""
    type: Literal['STOP_LOSS_PCT', 'STOP_LOSS_ATR'] = Field(..., description="Stop loss type")
    value: float = Field(..., description="Stop loss value (percentage or ATR multiplier)")


class TakeProfitConfig(BaseModel):
    """Take profit configuration"""
    type: Literal['TAKE_PROFIT_PCT', 'TAKE_PROFIT_ATR'] = Field(..., description="Take profit type")
    value: float = Field(..., description="Take profit value (percentage or ATR multiplier)")


class IndicatorExitConfig(BaseModel):
    """Indicator-based exit configuration"""
    type: Literal['INDICATOR_CROSS'] = 'INDICATOR_CROSS'
    indicator: str = Field(..., description="Indicator name")
    direction: Literal['UP', 'DOWN'] = Field(..., description="Cross direction")
    value: Optional[float] = Field(None, description="Threshold value (optional)")


class ConditionalOrFixedConfig(BaseModel):
    """Conditional or fixed exit configuration (OR logic)"""
    type: Literal['CONDITIONAL_OR_FIXED'] = 'CONDITIONAL_OR_FIXED'
    conditions: List[Union[StopLossConfig, TakeProfitConfig, IndicatorExitConfig]] = Field(
        ..., 
        description="List of exit conditions (OR logic - any condition triggers exit)"
    )


class SetupComponentConfig(BaseModel):
    """Setup component configuration (requirements JSON format)"""
    type: Literal['INDICATOR_THRESHOLD', 'NONE'] = Field(..., description="Setup type")
    timeframe: str = Field("1d", description="Candle interval for setup")
    indicator: Optional[str] = Field(None, description="Indicator name")
    params: Optional[Dict[str, Any]] = Field(None, description="Indicator parameters")
    operator: Optional[Literal['>', '<', '>=', '<=', '==', 'CROSS_ABOVE', 'CROSS_BELOW']] = Field(None, description="Comparison operator")
    value: Optional[float] = Field(None, description="Threshold value")
    indicator2: Optional[str] = Field(None, description="Second indicator for crossover")


class TriggerComponentConfig(BaseModel):
    """Trigger component configuration (requirements JSON format)"""
    type: Literal['CANDLE_PATTERN', 'PRICE_CROSSOVER', 'INDICATOR_CROSSOVER'] = Field(..., description="Trigger type")
    timeframe: str = Field("1d", description="Candle interval for trigger")
    pattern: Optional[str] = Field(None, description="Candle pattern (for CANDLE_PATTERN type)")
    price_level: Optional[float] = Field(None, description="Price level (for PRICE_CROSSOVER type)")
    indicator: Optional[str] = Field(None, description="Indicator name (for PRICE_CROSSOVER or INDICATOR_CROSSOVER)")
    indicator1: Optional[str] = Field(None, description="First indicator (for INDICATOR_CROSSOVER)")
    indicator2: Optional[str] = Field(None, description="Second indicator (for INDICATOR_CROSSOVER)")
    crossover_type: Optional[Literal['GOLDEN_CROSS', 'DEATH_CROSS']] = Field(None, description="Crossover type")
    direction: Optional[Literal['ABOVE', 'BELOW']] = Field(None, description="Direction (for PRICE_CROSSOVER)")


class ExitComponentConfig(BaseModel):
    """Exit component configuration (requirements JSON format)"""
    type: Literal['CONDITIONAL_OR_FIXED', 'STOP_LOSS_PCT', 'TAKE_PROFIT_PCT', 'TRAILING_STOP_PCT', 'TIME_BASED', 'INDICATOR_CROSS'] = Field(..., description="Exit type")
    timeframe: str = Field("1d", description="Candle interval for exit")
    conditions: Optional[List[Dict[str, Any]]] = Field(None, description="Exit conditions (for CONDITIONAL_OR_FIXED)")
    value: Optional[float] = Field(None, description="Exit value (for percentage-based exits)")
    indicator: Optional[str] = Field(None, description="Indicator name (for INDICATOR_CROSS)")
    direction: Optional[Literal['UP', 'DOWN']] = Field(None, description="Cross direction (for INDICATOR_CROSS)")
    max_holding_days: Optional[int] = Field(None, description="Max holding days (for TIME_BASED)")


class RequirementsStrategyConfig(BaseModel):
    """Strategy configuration matching requirements JSON format"""
    strategy_name: str = Field(..., description="Strategy name")
    symbol: Optional[str] = Field(None, description="Symbol (optional, for backtesting)")
    timeframe: str = Field("1d", description="Base timeframe for the strategy")
    components: Dict[str, Union[SetupComponentConfig, TriggerComponentConfig, ExitComponentConfig]] = Field(
        ...,
        description="Strategy components (setup, trigger, exit)"
    )
    initial_capital: Optional[float] = Field(None, description="Initial capital for backtesting")
    start_date: Optional[str] = Field(None, description="Start date for backtesting (YYYY-MM-DD)")
    end_date: Optional[str] = Field(None, description="End date for backtesting (YYYY-MM-DD)")


class ExpandableStrategyConfig(BaseModel):
    """Expandable strategy configuration (supports N steps, not just 3)"""
    name: str = Field(..., description="Strategy name")
    description: Optional[str] = Field(None, description="Strategy description")
    steps: List[StepConfig] = Field(..., min_items=1, description="List of strategy steps (expandable)")
    base_timeframe: str = Field("1d", description="Base timeframe for the strategy")
    
    # Optional filters
    min_market_cap: Optional[float] = Field(None, description="Minimum market cap filter")
    max_market_cap: Optional[float] = Field(None, description="Maximum market cap filter")
    sectors: Optional[List[str]] = Field(None, description="Allowed sectors")
    exclude_sectors: Optional[List[str]] = Field(None, description="Excluded sectors")
    
    # Performance tracking
    enabled: bool = Field(True, description="Whether strategy is enabled")
    priority: int = Field(0, description="Priority for scanning (higher = first)")
