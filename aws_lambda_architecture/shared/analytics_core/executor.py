"""
Multi-Timeframe Strategy Executor

Handles loading data from different timeframes and aligning signals
for multi-timeframe strategy execution.
"""

import polars as pl
from typing import Dict, List, Optional, Any
from datetime import date, datetime
from .inputs import load_ohlcv_by_timeframe
from .strategies.base import BaseStrategy
from .indicators.technicals import calculate_all_indicators
from .indicators.patterns import detect_all_patterns


class MultiTimeframeExecutor:
    """
    Executes strategies across multiple timeframes
    
    Handles:
    - Loading data for different timeframes (1d, 3d, 5d, etc.)
    - Executing strategy steps on appropriate timeframes
    - Aligning higher timeframe signals to base timeframe
    """
    
    def __init__(self, rds_connection_string: Optional[str] = None):
        """
        Initialize executor with RDS connection. All timeframes load from RDS (1d);
        resampled (3d, 5d, etc.) are computed at use from 1d.
        """
        self.rds_connection_string = rds_connection_string

    def load_multi_timeframe_data(
        self,
        symbol: str,
        timeframes: List[str],
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> Dict[str, pl.DataFrame]:
        """
        Load OHLCV for multiple timeframes from RDS. 1d from raw_ohlcv;
        3d, 5d, etc. from 1d resampled at use.
        """
        data_by_timeframe = {}
        if not self.rds_connection_string:
            raise ValueError("rds_connection_string is required.")
        for timeframe in timeframes:
            try:
                df = load_ohlcv_by_timeframe(
                    symbol=symbol,
                    timeframe=timeframe,
                    connection_string=self.rds_connection_string,
                    start_date=start_date,
                    end_date=end_date,
                )
                if df.height > 0:
                    if "date" not in df.columns and "timestamp" in df.columns:
                        df = df.with_columns(pl.col("timestamp").dt.date().alias("date"))
                    if "date" not in df.columns:
                        raise ValueError(f"No date/timestamp column in {timeframe} data")
                    df = df.sort("date")
                    if "symbol" not in df.columns:
                        df = df.with_columns(pl.lit(symbol).alias("symbol"))
                    data_by_timeframe[timeframe] = df
                else:
                    print(f"Warning: No data found for {symbol} at {timeframe} timeframe")
                    
            except Exception as e:
                print(f"Error loading {timeframe} data for {symbol}: {str(e)}")
                continue
        
        return data_by_timeframe
    
    def prepare_dataframe(self, df: pl.DataFrame, timeframe: str) -> pl.DataFrame:
        """
        Prepare dataframe by calculating indicators and patterns
        
        Args:
            df: OHLCV DataFrame
            timeframe: Timeframe string (for logging)
            
        Returns:
            DataFrame with indicators and patterns added
        """
        if df.height == 0:
            return df
        
        # Calculate technical indicators
        df = calculate_all_indicators(df)
        
        # Detect candle patterns
        df = detect_all_patterns(df)
        
        return df
    
    def align_timeframe_signals(
        self,
        base_df: pl.DataFrame,
        higher_timeframe_df: pl.DataFrame,
        higher_timeframe: str,
        signal_column: str = 'setup_valid'
    ) -> pl.DataFrame:
        """
        Align higher timeframe signals to base timeframe
        
        Example: If RSI > 50 on 3d candles, apply that signal to all 1d candles
        within that 3d period.
        
        Args:
            base_df: Base timeframe DataFrame (e.g., 1d)
            higher_timeframe_df: Higher timeframe DataFrame (e.g., 3d)
            higher_timeframe: Higher timeframe string (e.g., '3d')
            signal_column: Column name to align (default: 'setup_valid')
            
        Returns:
            Base DataFrame with aligned signal column added/updated
        """
        if signal_column not in higher_timeframe_df.columns:
            return base_df
        
        # Create a mapping: for each base date, find the corresponding higher timeframe signal
        # Strategy: Forward-fill the higher timeframe signal to all base candles in that period
        
        # Merge higher timeframe signals onto base timeframe
        # Use date alignment (base date falls within higher timeframe period)
        base_with_signals = base_df.join_asof(
            higher_timeframe_df.select(['date', signal_column]).sort('date'),
            left_on='date',
            right_on='date',
            strategy='backward'  # Use most recent higher timeframe signal
        )
        
        return base_with_signals
    
    def execute_strategy_multi_timeframe(
        self,
        strategy: BaseStrategy,
        data_by_timeframe: Dict[str, pl.DataFrame],
        base_timeframe: str = '1d'
    ) -> pl.DataFrame:
        """
        Execute strategy across multiple timeframes
        
        Steps:
        1. Prepare data for each timeframe (calculate indicators, patterns)
        2. Execute strategy steps on appropriate timeframes
        3. Align higher timeframe signals to base timeframe
        4. Merge all signals into base timeframe DataFrame
        
        Args:
            strategy: Strategy instance (BaseStrategy or subclass)
            data_by_timeframe: Dictionary of timeframes to DataFrames
            base_timeframe: Base timeframe for final output (default: '1d')
            
        Returns:
            Base timeframe DataFrame with all strategy signals
        """
        if base_timeframe not in data_by_timeframe:
            raise ValueError(f"Base timeframe {base_timeframe} not found in data")
        
        # Prepare base timeframe data
        base_df = data_by_timeframe[base_timeframe].clone()
        base_df = self.prepare_dataframe(base_df, base_timeframe)
        
        # If strategy uses expandable steps, execute each step on its timeframe
        if strategy._use_expandable_mode and strategy.steps:
            for step in strategy.steps:
                if not step.enabled:
                    continue
                
                step_timeframe = step.timeframe
                
                # Load and prepare data for this step's timeframe
                if step_timeframe in data_by_timeframe:
                    step_df = data_by_timeframe[step_timeframe].clone()
                    step_df = self.prepare_dataframe(step_df, step_timeframe)
                    
                    # Execute step on its timeframe
                    step_df = strategy.execute_step(step, step_df)
                    
                    # If step timeframe is different from base, align signals
                    if step_timeframe != base_timeframe:
                        # Find signal columns added by this step
                        new_columns = [col for col in step_df.columns if col not in base_df.columns]
                        for col in new_columns:
                            base_df = self.align_timeframe_signals(
                                base_df, step_df, step_timeframe, col
                            )
                    else:
                        # Same timeframe, merge directly
                        base_df = step_df
                else:
                    print(f"Warning: Timeframe {step_timeframe} not available, skipping step {step.step_name}")
        
        else:
            # Legacy 3-step mode: execute on base timeframe
            base_df = strategy.run(base_df)
        
        return base_df
    
    def execute_strategy(
        self,
        strategy: BaseStrategy,
        symbol: str,
        timeframes: List[str],
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        base_timeframe: str = "1d",
    ) -> pl.DataFrame:
        """
        Load data from RDS (1d + resample at use) and execute strategy.
        """
        data_by_timeframe = self.load_multi_timeframe_data(
            symbol=symbol,
            timeframes=timeframes,
            start_date=start_date,
            end_date=end_date,
        )
        
        if not data_by_timeframe:
            raise ValueError(f"No data loaded for {symbol}")
        
        # Execute strategy
        result_df = self.execute_strategy_multi_timeframe(
            strategy=strategy,
            data_by_timeframe=data_by_timeframe,
            base_timeframe=base_timeframe
        )
        
        return result_df
