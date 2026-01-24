"""
Composite Strategy Builder

Allows users to mix-and-match Setup, Trigger, and Exit configurations
without writing new code. Builds a CompositeStrategy from JSON config.

Supports both legacy StrategyConfig format and new RequirementsStrategyConfig format.
"""

import polars as pl
from typing import Dict, Any, Optional
from ..models import (
    StrategyConfig, SetupConfig, TriggerConfig, ExitConfig,
    RequirementsStrategyConfig, SetupComponentConfig, TriggerComponentConfig, ExitComponentConfig,
    StepConfig
)
from .base import BaseStrategy
from ..indicators.patterns import (
    detect_engulfing_bullish, detect_engulfing_bearish, detect_hammer,
    detect_shooting_star, detect_doji, detect_morning_star, detect_evening_star,
    detect_green_candle, detect_red_candle
)
from ..indicators.technicals import (
    calculate_rsi, calculate_sma, calculate_ema, calculate_macd
)


class CompositeStrategy(BaseStrategy):
    """
    Composite Strategy - Built from user configuration
    
    Combines Setup, Trigger, and Exit configurations into a single strategy.
    Supports both legacy StrategyConfig and new RequirementsStrategyConfig formats.
    """
    
    def __init__(self, config: Optional[StrategyConfig] = None, requirements_config: Optional[RequirementsStrategyConfig] = None):
        """
        Initialize composite strategy from configuration
        
        Args:
            config: Legacy StrategyConfig (optional)
            requirements_config: New RequirementsStrategyConfig (optional)
        """
        if config:
            super().__init__(name=config.name, description=config.description)
            self.config = config
            self.setup_config = config.setup
            self.trigger_config = config.trigger
            self.exit_config = config.exit
            self.requirements_config = None
            self._use_requirements_format = False
        elif requirements_config:
            super().__init__(name=requirements_config.strategy_name, description=None)
            self.config = None
            self.requirements_config = requirements_config
            self._use_requirements_format = True
            # Create step configs for expandable mode
            steps = []
            if 'setup' in requirements_config.components:
                steps.append(StepConfig(
                    step_name='setup',
                    timeframe=requirements_config.components['setup'].timeframe,
                    enabled=True
                ))
            if 'trigger' in requirements_config.components:
                steps.append(StepConfig(
                    step_name='trigger',
                    timeframe=requirements_config.components['trigger'].timeframe,
                    enabled=True
                ))
            if 'exit' in requirements_config.components:
                steps.append(StepConfig(
                    step_name='exit',
                    timeframe=requirements_config.components['exit'].timeframe,
                    enabled=True
                ))
            self.steps = steps
        else:
            raise ValueError("Either config or requirements_config must be provided")
    
    @classmethod
    def from_requirements_json(cls, config_dict: Dict[str, Any]) -> 'CompositeStrategy':
        """
        Create CompositeStrategy from requirements JSON format
        
        Args:
            config_dict: Dictionary matching RequirementsStrategyConfig format
            
        Returns:
            CompositeStrategy instance
        """
        requirements_config = RequirementsStrategyConfig(**config_dict)
        return cls(requirements_config=requirements_config)
    
    def setup(self, df: pl.DataFrame) -> pl.DataFrame:
        """Apply setup (momentum) logic based on configuration"""
        setup_type = self.setup_config.type
        
        if setup_type == 'NONE':
            # No setup filter - always valid
            return df.with_columns(pl.lit(True).alias('setup_valid'))
        
        elif setup_type == 'RSI_MOMENTUM':
            return self._setup_rsi_momentum(df)
        
        elif setup_type == 'SMA_TREND':
            return self._setup_sma_trend(df)
        
        elif setup_type == 'MACD_TREND':
            return self._setup_macd_trend(df)
        
        elif setup_type == 'VOLUME_TREND':
            return self._setup_volume_trend(df)
        
        else:
            raise ValueError(f"Unknown setup type: {setup_type}")
    
    def trigger(self, df: pl.DataFrame) -> pl.DataFrame:
        """Apply trigger (entry) logic based on configuration"""
        trigger_type = self.trigger_config.type
        
        # Initialize signal column as HOLD
        df = df.with_columns(pl.lit('HOLD').alias('signal'))
        
        # Only generate signals when setup is valid
        if trigger_type == 'CANDLE_PATTERN':
            return self._trigger_candle_pattern(df)
        
        elif trigger_type == 'PRICE_CROSSOVER':
            return self._trigger_price_crossover(df)
        
        elif trigger_type == 'INDICATOR_CROSSOVER':
            return self._trigger_indicator_crossover(df)
        
        elif trigger_type == 'BREAKOUT':
            return self._trigger_breakout(df)
        
        elif trigger_type == 'REVERSAL':
            return self._trigger_reversal(df)
        
        else:
            raise ValueError(f"Unknown trigger type: {trigger_type}")
    
    def exit(self, df: pl.DataFrame) -> pl.DataFrame:
        """Apply exit (management) logic based on configuration"""
        exit_type = self.exit_config.type
        
        # Initialize exit columns
        df = df.with_columns([
            pl.lit(None).alias('exit_signal'),
            pl.lit(None).cast(pl.Float64).alias('exit_price'),
        ])
        
        if exit_type == 'STOP_LOSS':
            return self._exit_stop_loss(df)
        
        elif exit_type == 'TAKE_PROFIT':
            return self._exit_take_profit(df)
        
        elif exit_type == 'TRAILING_STOP':
            return self._exit_trailing_stop(df)
        
        elif exit_type == 'TIME_BASED':
            return self._exit_time_based(df)
        
        elif exit_type == 'INDICATOR_SIGNAL':
            return self._exit_indicator_signal(df)
        
        elif exit_type == 'COMBINED':
            return self._exit_combined(df)
        
        else:
            raise ValueError(f"Unknown exit type: {exit_type}")
    
    # Setup Methods
    
    def _setup_rsi_momentum(self, df: pl.DataFrame) -> pl.DataFrame:
        """RSI momentum filter"""
        conditions = []
        
        if self.setup_config.min_rsi is not None:
            conditions.append(pl.col('rsi') >= self.setup_config.min_rsi)
        
        if self.setup_config.max_rsi is not None:
            conditions.append(pl.col('rsi') <= self.setup_config.max_rsi)
        
        if not conditions:
            return df.with_columns(pl.lit(True).alias('setup_valid'))
        
        return df.with_columns(
            pl.all_horizontal(conditions).alias('setup_valid')
        )
    
    def _setup_sma_trend(self, df: pl.DataFrame) -> pl.DataFrame:
        """SMA trend filter"""
        fast = self.setup_config.fast_period
        slow = self.setup_config.slow_period
        direction = self.setup_config.direction
        
        fast_col = f'sma_{fast}'
        slow_col = f'sma_{slow}'
        
        if direction == 'ABOVE':
            return df.with_columns(
                (pl.col(fast_col) > pl.col(slow_col)).alias('setup_valid')
            )
        elif direction == 'BELOW':
            return df.with_columns(
                (pl.col(fast_col) < pl.col(slow_col)).alias('setup_valid')
            )
        else:
            return df.with_columns(pl.lit(True).alias('setup_valid'))
    
    def _setup_macd_trend(self, df: pl.DataFrame) -> pl.DataFrame:
        """MACD trend filter"""
        signal = self.setup_config.macd_signal
        
        if signal == 'BULLISH':
            return df.with_columns(
                (pl.col('macd') > pl.col('macd_signal')).alias('setup_valid')
            )
        elif signal == 'BEARISH':
            return df.with_columns(
                (pl.col('macd') < pl.col('macd_signal')).alias('setup_valid')
            )
        else:
            return df.with_columns(pl.lit(True).alias('setup_valid'))
    
    def _setup_volume_trend(self, df: pl.DataFrame) -> pl.DataFrame:
        """Volume trend filter"""
        multiplier = self.setup_config.volume_multiplier or 1.0
        
        # Calculate average volume
        avg_volume = df['volume'].mean()
        
        return df.with_columns(
            (pl.col('volume') >= (avg_volume * multiplier)).alias('setup_valid')
        )
    
    # Trigger Methods
    
    def _trigger_candle_pattern(self, df: pl.DataFrame) -> pl.DataFrame:
        """Candle pattern trigger"""
        pattern = self.trigger_config.pattern
        
        # Only trigger when setup is valid
        setup_mask = pl.col('setup_valid')
        
        if pattern == 'ENGULFING_BULLISH':
            # Current candle engulfs previous candle (bullish)
            bullish_engulfing = (
                (pl.col('open') < pl.col('close').shift(1)) &  # Current opens below prev close
                (pl.col('close') > pl.col('open').shift(1)) &  # Current closes above prev open
                (pl.col('close') > pl.col('open'))              # Current is bullish
            )
            return df.with_columns(
                pl.when(setup_mask & bullish_engulfing)
                .then(pl.lit('BUY'))
                .otherwise(pl.col('signal'))
                .alias('signal')
            )
        
        elif pattern == 'ENGULFING_BEARISH':
            bearish_engulfing = (
                (pl.col('open') > pl.col('close').shift(1)) &
                (pl.col('close') < pl.col('open').shift(1)) &
                (pl.col('close') < pl.col('open'))
            )
            return df.with_columns(
                pl.when(setup_mask & bearish_engulfing)
                .then(pl.lit('SELL'))
                .otherwise(pl.col('signal'))
                .alias('signal')
            )
        
        # Add more patterns as needed
        else:
            return df
    
    def _trigger_price_crossover(self, df: pl.DataFrame) -> pl.DataFrame:
        """Price crossover trigger"""
        price_level = self.trigger_config.price_level
        direction = self.trigger_config.direction
        
        setup_mask = pl.col('setup_valid')
        
        if direction == 'ABOVE':
            crossover = (
                (pl.col('close') > price_level) &
                (pl.col('close').shift(1) <= price_level)
            )
            return df.with_columns(
                pl.when(setup_mask & crossover)
                .then(pl.lit('BUY'))
                .otherwise(pl.col('signal'))
                .alias('signal')
            )
        
        elif direction == 'BELOW':
            crossover = (
                (pl.col('close') < price_level) &
                (pl.col('close').shift(1) >= price_level)
            )
            return df.with_columns(
                pl.when(setup_mask & crossover)
                .then(pl.lit('SELL'))
                .otherwise(pl.col('signal'))
                .alias('signal')
            )
        
        return df
    
    def _trigger_indicator_crossover(self, df: pl.DataFrame) -> pl.DataFrame:
        """Indicator crossover trigger (e.g., Golden Cross)"""
        indicator1 = self.trigger_config.indicator1
        indicator2 = self.trigger_config.indicator2
        crossover_type = self.trigger_config.crossover_type
        
        setup_mask = pl.col('setup_valid')
        
        if crossover_type == 'GOLDEN_CROSS':
            # Fast crosses above slow
            golden_cross = (
                (pl.col(indicator1) > pl.col(indicator2)) &
                (pl.col(indicator1).shift(1) <= pl.col(indicator2).shift(1))
            )
            return df.with_columns(
                pl.when(setup_mask & golden_cross)
                .then(pl.lit('BUY'))
                .otherwise(pl.col('signal'))
                .alias('signal')
            )
        
        elif crossover_type == 'DEATH_CROSS':
            # Fast crosses below slow
            death_cross = (
                (pl.col(indicator1) < pl.col(indicator2)) &
                (pl.col(indicator1).shift(1) >= pl.col(indicator2).shift(1))
            )
            return df.with_columns(
                pl.when(setup_mask & death_cross)
                .then(pl.lit('SELL'))
                .otherwise(pl.col('signal'))
                .alias('signal')
            )
        
        return df
    
    def _trigger_breakout(self, df: pl.DataFrame) -> pl.DataFrame:
        """Breakout trigger"""
        breakout_type = self.trigger_config.breakout_type
        confirmation_bars = self.trigger_config.confirmation_bars or 1
        
        setup_mask = pl.col('setup_valid')
        
        if breakout_type == 'BOLLINGER_UPPER':
            # Price breaks above upper Bollinger Band
            breakout = (
                (pl.col('close') > pl.col('bb_upper')) &
                (pl.col('close').shift(1) <= pl.col('bb_upper').shift(1))
            )
            return df.with_columns(
                pl.when(setup_mask & breakout)
                .then(pl.lit('BUY'))
                .otherwise(pl.col('signal'))
                .alias('signal')
            )
        
        # Add more breakout types as needed
        return df
    
    def _trigger_reversal(self, df: pl.DataFrame) -> pl.DataFrame:
        """Reversal trigger (e.g., RSI oversold bounce)"""
        reversal_type = self.trigger_config.reversal_type
        
        setup_mask = pl.col('setup_valid')
        
        if reversal_type == 'RSI_OVERSOLD':
            # RSI was oversold (<30) and now bouncing back
            reversal = (
                (pl.col('rsi') > 30) &
                (pl.col('rsi').shift(1) <= 30) &
                (pl.col('close') > pl.col('open'))  # Bullish candle
            )
            return df.with_columns(
                pl.when(setup_mask & reversal)
                .then(pl.lit('BUY'))
                .otherwise(pl.col('signal'))
                .alias('signal')
            )
        
        elif reversal_type == 'RSI_OVERBOUGHT':
            # RSI was overbought (>70) and now reversing
            reversal = (
                (pl.col('rsi') < 70) &
                (pl.col('rsi').shift(1) >= 70) &
                (pl.col('close') < pl.col('open'))  # Bearish candle
            )
            return df.with_columns(
                pl.when(setup_mask & reversal)
                .then(pl.lit('SELL'))
                .otherwise(pl.col('signal'))
                .alias('signal')
            )
        
        return df
    
    # Exit Methods
    
    def _exit_stop_loss(self, df: pl.DataFrame) -> pl.DataFrame:
        """Stop loss exit"""
        stop_pct = self.exit_config.stop_loss_pct
        
        if stop_pct is None:
            return df
        
        # Calculate stop loss price for each buy signal
        # This is simplified - in practice, you'd track entry price per position
        return df.with_columns([
            pl.when(pl.col('signal') == 'BUY')
            .then(pl.col('close') * (1 - stop_pct))
            .otherwise(None)
            .alias('stop_loss_price')
        ])
    
    def _exit_take_profit(self, df: pl.DataFrame) -> pl.DataFrame:
        """Take profit exit"""
        profit_pct = self.exit_config.take_profit_pct
        
        if profit_pct is None:
            return df
        
        return df.with_columns([
            pl.when(pl.col('signal') == 'BUY')
            .then(pl.col('close') * (1 + profit_pct))
            .otherwise(None)
            .alias('take_profit_price')
        ])
    
    def _exit_trailing_stop(self, df: pl.DataFrame) -> pl.DataFrame:
        """Trailing stop exit"""
        trailing_pct = self.exit_config.trailing_stop_pct
        
        if trailing_pct is None:
            return df
        
        # Simplified trailing stop logic
        # In practice, you'd track highest price since entry
        return df.with_columns([
            pl.col('close').rolling_max(window_size=20).alias('trailing_stop_price')
        ])
    
    def _exit_time_based(self, df: pl.DataFrame) -> pl.DataFrame:
        """Time-based exit"""
        max_days = self.exit_config.max_holding_days
        
        if max_days is None:
            return df
        
        # This would require tracking entry dates per position
        # Simplified for now
        return df
    
    def _exit_indicator_signal(self, df: pl.DataFrame) -> pl.DataFrame:
        """Indicator-based exit signal"""
        # Implementation depends on specific indicator
        return df
    
    def _exit_combined(self, df: pl.DataFrame) -> pl.DataFrame:
        """Combined exit rules (multiple conditions)"""
        # Apply multiple exit rules with OR logic
        return df
    
    # ============================================================================
    # Requirements JSON Format Support (Expandable Mode)
    # ============================================================================
    
    def execute_step(self, step: StepConfig, df: pl.DataFrame) -> pl.DataFrame:
        """
        Execute a single step (for expandable mode with requirements JSON)
        
        Args:
            step: StepConfig with step_name, timeframe, enabled
            df: DataFrame with data at step's timeframe
            
        Returns:
            DataFrame with step results
        """
        if not self._use_requirements_format:
            raise ValueError("execute_step() only works with requirements format")
        
        step_name = step.step_name
        component = self.requirements_config.components.get(step_name)
        
        if not component:
            return df
        
        if step_name == 'setup':
            return self._execute_setup_requirements(component, df)
        elif step_name == 'trigger':
            return self._execute_trigger_requirements(component, df)
        elif step_name == 'exit':
            return self._execute_exit_requirements(component, df)
        else:
            raise ValueError(f"Unknown step name: {step_name}")
    
    def _execute_setup_requirements(self, setup: SetupComponentConfig, df: pl.DataFrame) -> pl.DataFrame:
        """Execute setup step in requirements format"""
        if setup.type == 'NONE':
            return df.with_columns(pl.lit(True).alias('setup_valid'))
        
        elif setup.type == 'INDICATOR_THRESHOLD':
            return self._setup_indicator_threshold(setup, df)
        
        else:
            raise ValueError(f"Unknown setup type: {setup.type}")
    
    def _setup_indicator_threshold(self, setup: SetupComponentConfig, df: pl.DataFrame) -> pl.DataFrame:
        """Setup using indicator threshold (requirements format)"""
        indicator = setup.indicator.upper()
        operator = setup.operator
        value = setup.value
        
        # Calculate indicator if not present
        if indicator == 'RSI':
            period = setup.params.get('period', 14) if setup.params else 14
            if 'rsi' not in df.columns:
                df = calculate_rsi(df, period=period)
            indicator_col = 'rsi'
        elif indicator.startswith('SMA'):
            period = setup.params.get('period', 20) if setup.params else 20
            indicator_col = f'sma_{period}'
            if indicator_col not in df.columns:
                df = calculate_sma(df, period=period)
        elif indicator.startswith('EMA'):
            period = setup.params.get('period', 12) if setup.params else 12
            indicator_col = f'ema_{period}'
            if indicator_col not in df.columns:
                df = calculate_ema(df, period=period)
        else:
            raise ValueError(f"Unsupported indicator for threshold: {indicator}")
        
        # Apply operator
        if operator == 'CROSS_ABOVE':
            # Indicator crosses above value or another indicator
            if setup.indicator2:
                indicator2_col = setup.indicator2
                condition = (pl.col(indicator_col) > pl.col(indicator2_col)) & \
                           (pl.col(indicator_col).shift(1) <= pl.col(indicator2_col).shift(1))
            else:
                condition = (pl.col(indicator_col) > value) & \
                           (pl.col(indicator_col).shift(1) <= value)
        elif operator == 'CROSS_BELOW':
            if setup.indicator2:
                indicator2_col = setup.indicator2
                condition = (pl.col(indicator_col) < pl.col(indicator2_col)) & \
                           (pl.col(indicator_col).shift(1) >= pl.col(indicator2_col).shift(1))
            else:
                condition = (pl.col(indicator_col) < value) & \
                           (pl.col(indicator_col).shift(1) >= value)
        elif operator == '>':
            condition = pl.col(indicator_col) > value
        elif operator == '<':
            condition = pl.col(indicator_col) < value
        elif operator == '>=':
            condition = pl.col(indicator_col) >= value
        elif operator == '<=':
            condition = pl.col(indicator_col) <= value
        elif operator == '==':
            condition = pl.col(indicator_col) == value
        else:
            raise ValueError(f"Unsupported operator: {operator}")
        
        return df.with_columns(condition.alias('setup_valid'))
    
    def _execute_trigger_requirements(self, trigger: TriggerComponentConfig, df: pl.DataFrame) -> pl.DataFrame:
        """Execute trigger step in requirements format"""
        # Initialize signal column
        if 'signal' not in df.columns:
            df = df.with_columns(pl.lit('HOLD').alias('signal'))
        
        if trigger.type == 'CANDLE_PATTERN':
            return self._trigger_candle_pattern_requirements(trigger, df)
        elif trigger.type == 'PRICE_CROSSOVER':
            return self._trigger_price_crossover_requirements(trigger, df)
        elif trigger.type == 'INDICATOR_CROSSOVER':
            return self._trigger_indicator_crossover_requirements(trigger, df)
        else:
            raise ValueError(f"Unknown trigger type: {trigger.type}")
    
    def _trigger_candle_pattern_requirements(self, trigger: TriggerComponentConfig, df: pl.DataFrame) -> pl.DataFrame:
        """Trigger using candle pattern (requirements format)"""
        pattern = trigger.pattern
        
        # Detect patterns
        if pattern in ['BULLISH_ENGULFING', 'ENGULFING_BULLISH']:
            df = detect_engulfing_bullish(df)
            condition = pl.col('engulfing_bullish')
        elif pattern in ['BEARISH_ENGULFING', 'ENGULFING_BEARISH']:
            df = detect_engulfing_bearish(df)
            condition = pl.col('engulfing_bearish')
        elif pattern == 'HAMMER':
            df = detect_hammer(df)
            condition = pl.col('hammer')
        elif pattern == 'SHOOTING_STAR':
            df = detect_shooting_star(df)
            condition = pl.col('shooting_star')
        elif pattern == 'DOJI':
            df = detect_doji(df)
            condition = pl.col('doji')
        elif pattern == 'MORNING_STAR':
            df = detect_morning_star(df)
            condition = pl.col('morning_star')
        elif pattern == 'EVENING_STAR':
            df = detect_evening_star(df)
            condition = pl.col('evening_star')
        elif pattern == 'GREEN_CANDLE':
            df = detect_green_candle(df)
            condition = pl.col('green_candle')
        elif pattern == 'RED_CANDLE':
            df = detect_red_candle(df)
            condition = pl.col('red_candle')
        else:
            raise ValueError(f"Unknown candle pattern: {pattern}")
        
        # Only trigger when setup is valid
        setup_mask = pl.col('setup_valid') if 'setup_valid' in df.columns else pl.lit(True)
        signal_value = 'BUY' if 'BULLISH' in pattern or pattern in ['HAMMER', 'MORNING_STAR', 'GREEN_CANDLE'] else 'SELL'
        
        return df.with_columns(
            pl.when(setup_mask & condition)
            .then(pl.lit(signal_value))
            .otherwise(pl.col('signal'))
            .alias('signal')
        )
    
    def _trigger_price_crossover_requirements(self, trigger: TriggerComponentConfig, df: pl.DataFrame) -> pl.DataFrame:
        """Trigger using price crossover (requirements format)"""
        setup_mask = pl.col('setup_valid') if 'setup_valid' in df.columns else pl.lit(True)
        
        if trigger.price_level:
            # Price crosses above/below fixed level
            if trigger.direction == 'ABOVE':
                crossover = (pl.col('close') > trigger.price_level) & \
                          (pl.col('close').shift(1) <= trigger.price_level)
            else:
                crossover = (pl.col('close') < trigger.price_level) & \
                          (pl.col('close').shift(1) >= trigger.price_level)
        elif trigger.indicator:
            # Price crosses above/below indicator
            indicator_col = trigger.indicator
            if trigger.direction == 'ABOVE':
                crossover = (pl.col('close') > pl.col(indicator_col)) & \
                          (pl.col('close').shift(1) <= pl.col(indicator_col).shift(1))
            else:
                crossover = (pl.col('close') < pl.col(indicator_col)) & \
                          (pl.col('close').shift(1) >= pl.col(indicator_col).shift(1))
        else:
            raise ValueError("Either price_level or indicator must be specified")
        
        signal_value = 'BUY' if trigger.direction == 'ABOVE' else 'SELL'
        
        return df.with_columns(
            pl.when(setup_mask & crossover)
            .then(pl.lit(signal_value))
            .otherwise(pl.col('signal'))
            .alias('signal')
        )
    
    def _trigger_indicator_crossover_requirements(self, trigger: TriggerComponentConfig, df: pl.DataFrame) -> pl.DataFrame:
        """Trigger using indicator crossover (requirements format)"""
        setup_mask = pl.col('setup_valid') if 'setup_valid' in df.columns else pl.lit(True)
        
        indicator1 = trigger.indicator1 or trigger.indicator
        indicator2 = trigger.indicator2
        
        if trigger.crossover_type == 'GOLDEN_CROSS':
            crossover = (pl.col(indicator1) > pl.col(indicator2)) & \
                       (pl.col(indicator1).shift(1) <= pl.col(indicator2).shift(1))
            signal_value = 'BUY'
        elif trigger.crossover_type == 'DEATH_CROSS':
            crossover = (pl.col(indicator1) < pl.col(indicator2)) & \
                       (pl.col(indicator1).shift(1) >= pl.col(indicator2).shift(1))
            signal_value = 'SELL'
        else:
            raise ValueError(f"Unknown crossover type: {trigger.crossover_type}")
        
        return df.with_columns(
            pl.when(setup_mask & crossover)
            .then(pl.lit(signal_value))
            .otherwise(pl.col('signal'))
            .alias('signal')
        )
    
    def _execute_exit_requirements(self, exit: ExitComponentConfig, df: pl.DataFrame) -> pl.DataFrame:
        """Execute exit step in requirements format"""
        if exit.type == 'CONDITIONAL_OR_FIXED':
            return self._exit_conditional_or_fixed(exit, df)
        elif exit.type == 'STOP_LOSS_PCT':
            return self._exit_stop_loss_pct(exit, df)
        elif exit.type == 'TAKE_PROFIT_PCT':
            return self._exit_take_profit_pct(exit, df)
        elif exit.type == 'TRAILING_STOP_PCT':
            return self._exit_trailing_stop_pct(exit, df)
        elif exit.type == 'TIME_BASED':
            return self._exit_time_based_requirements(exit, df)
        elif exit.type == 'INDICATOR_CROSS':
            return self._exit_indicator_cross(exit, df)
        else:
            raise ValueError(f"Unknown exit type: {exit.type}")
    
    def _exit_conditional_or_fixed(self, exit: ExitComponentConfig, df: pl.DataFrame) -> pl.DataFrame:
        """Exit with conditional OR fixed rules (requirements format)"""
        # Initialize exit columns
        if 'exit_signal' not in df.columns:
            df = df.with_columns([
                pl.lit(None).alias('exit_signal'),
                pl.lit(None).cast(pl.Float64).alias('exit_price'),
            ])
        
        # Apply each condition (OR logic - any condition triggers exit)
        exit_conditions = []
        
        for condition_dict in exit.conditions or []:
            cond_type = condition_dict.get('type')
            
            if cond_type == 'STOP_LOSS_PCT':
                stop_pct = condition_dict.get('value', 0.05)
                stop_price = pl.col('close') * (1 - stop_pct)
                exit_conditions.append(pl.col('close') <= stop_price)
            
            elif cond_type == 'TAKE_PROFIT_PCT':
                profit_pct = condition_dict.get('value', 0.10)
                profit_price = pl.col('close') * (1 + profit_pct)
                exit_conditions.append(pl.col('close') >= profit_price)
            
            elif cond_type == 'INDICATOR_CROSS':
                indicator = condition_dict.get('indicator')
                direction = condition_dict.get('direction', 'DOWN')
                if direction == 'DOWN':
                    exit_conditions.append(
                        (pl.col(indicator) < pl.col(indicator).shift(1)) &
                        (pl.col(indicator).shift(1) >= condition_dict.get('value', 70))
                    )
                else:
                    exit_conditions.append(
                        (pl.col(indicator) > pl.col(indicator).shift(1)) &
                        (pl.col(indicator).shift(1) <= condition_dict.get('value', 30))
                    )
        
        # Combine with OR logic
        if exit_conditions:
            combined_condition = exit_conditions[0]
            for cond in exit_conditions[1:]:
                combined_condition = combined_condition | cond
            
            return df.with_columns(
                pl.when(combined_condition)
                .then(pl.lit('SELL'))
                .otherwise(pl.col('exit_signal'))
                .alias('exit_signal')
            )
        
        return df
    
    def _exit_stop_loss_pct(self, exit: ExitComponentConfig, df: pl.DataFrame) -> pl.DataFrame:
        """Exit with stop loss percentage"""
        stop_pct = exit.value or 0.05
        return df.with_columns([
            pl.when(pl.col('signal') == 'BUY')
            .then(pl.col('close') * (1 - stop_pct))
            .otherwise(None)
            .alias('stop_loss_price')
        ])
    
    def _exit_take_profit_pct(self, exit: ExitComponentConfig, df: pl.DataFrame) -> pl.DataFrame:
        """Exit with take profit percentage"""
        profit_pct = exit.value or 0.10
        return df.with_columns([
            pl.when(pl.col('signal') == 'BUY')
            .then(pl.col('close') * (1 + profit_pct))
            .otherwise(None)
            .alias('take_profit_price')
        ])
    
    def _exit_trailing_stop_pct(self, exit: ExitComponentConfig, df: pl.DataFrame) -> pl.DataFrame:
        """Exit with trailing stop percentage"""
        trailing_pct = exit.value or 0.03
        return df.with_columns([
            pl.col('close').rolling_max(window_size=20).alias('trailing_stop_price')
        ])
    
    def _exit_time_based_requirements(self, exit: ExitComponentConfig, df: pl.DataFrame) -> pl.DataFrame:
        """Exit based on time (requirements format)"""
        # Simplified - would need position tracking for full implementation
        return df
    
    def _exit_indicator_cross(self, exit: ExitComponentConfig, df: pl.DataFrame) -> pl.DataFrame:
        """Exit based on indicator cross (requirements format)"""
        indicator = exit.indicator
        direction = exit.direction or 'DOWN'
        value = exit.value
        
        if direction == 'DOWN':
            cross_condition = (pl.col(indicator) < value) & \
                             (pl.col(indicator).shift(1) >= value)
        else:
            cross_condition = (pl.col(indicator) > value) & \
                             (pl.col(indicator).shift(1) <= value)
        
        return df.with_columns(
            pl.when(cross_condition)
            .then(pl.lit('SELL'))
            .otherwise(None)
            .alias('exit_signal')
        )