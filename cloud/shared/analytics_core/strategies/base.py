"""
Base Strategy Class - Supports Expandable Step-Based Architecture

Legacy 3-step structure (setup/trigger/exit) is still supported for backward compatibility.
New expandable architecture supports N steps with individual timeframes.

Partition awareness
--------------------
A strategy may be evaluated on a *single-symbol* frame (the backtester / serving
layer) or on a *multi-symbol* long-format frame (the full-universe vectorized
scanner). Cross-row operations (``shift``, ``rolling_*``, ``ewm_mean``,
``diff``, aggregates) must NOT bleed across symbols on a multi-symbol frame.

To support both with a single implementation, ``run()`` accepts an optional
``partition_by`` key. Subclasses wrap any cross-row expression in
``self._w(expr)``: when a partition is active it expands to
``expr.over(partition_by)``; otherwise the expression is returned unchanged so
the single-symbol path stays byte-for-byte identical to before.
"""

from abc import ABC, abstractmethod
from typing import Literal, Optional, Dict, Any, List
import polars as pl
from ..models import StepConfig


class BaseStrategy(ABC):
    """
    Base class for all trading strategies
    
    Supports two modes:
    1. Legacy 3-step mode: setup() -> trigger() -> exit()
    2. Expandable step mode: steps: List[StepConfig] with execute_step()
    
    The expandable mode allows for N steps (not just 3), each with its own timeframe.
    """
    
    def __init__(self, name: str, description: Optional[str] = None, steps: Optional[List[StepConfig]] = None):
        self.name = name
        self.description = description
        self.steps = steps  # Expandable steps (None = use legacy 3-step mode)
        self._use_expandable_mode = steps is not None and len(steps) > 0
        # Active partition key for the current run() call. None => single-symbol
        # frame; cross-row ops run unpartitioned (legacy behavior).
        self._partition_by: Optional[str] = None

    def _w(self, expr: pl.Expr) -> pl.Expr:
        """
        Partition a cross-row expression when running on a multi-symbol frame.

        Returns ``expr.over(self._partition_by)`` while a partition is active
        (e.g. ``"symbol"`` in the vectorized scanner) and ``expr`` unchanged
        otherwise. Wrap EVERY ``shift`` / ``rolling_*`` / ``ewm_mean`` / ``diff``
        / aggregate that must stay within a single symbol.
        """
        if self._partition_by:
            return expr.over(self._partition_by)
        return expr
    
    @abstractmethod
    def setup(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Step 1: Setup (Momentum)
        
        Validates if the market environment is favorable for entry.
        Adds a boolean column 'setup_valid' to the dataframe.
        
        Args:
            df: OHLCV dataframe with indicators already calculated
            
        Returns:
            DataFrame with 'setup_valid' column added
        """
        pass
    
    @abstractmethod
    def trigger(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Step 2: Trigger (Pattern)
        
        Detects entry signals ONLY when setup_valid is True.
        Adds a 'signal' column with values: 'BUY', 'SELL', 'HOLD'
        
        Args:
            df: DataFrame with 'setup_valid' column from setup()
            
        Returns:
            DataFrame with 'signal' column added
        """
        pass
    
    @abstractmethod
    def exit(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Step 3: Exit (Management)
        
        Determines when to exit positions.
        Adds 'exit_signal' and 'exit_price' columns.
        
        Args:
            df: DataFrame with 'signal' column from trigger()
            
        Returns:
            DataFrame with exit logic columns added
        """
        pass
    
    def run(self, df: pl.DataFrame, partition_by: Optional[str] = None) -> pl.DataFrame:
        """
        Execute the strategy (supports both legacy 3-step and expandable modes)
        
        Args:
            df: OHLCV dataframe (must have indicators pre-calculated). When
                ``partition_by`` is set, ``df`` may contain many symbols stacked
                long-format and MUST be sorted by ``[partition_by, date]`` so
                rolling / shift windows are well-defined per group.
            partition_by: optional column (e.g. ``"symbol"``) that bounds all
                cross-row operations to a single group. ``None`` (default)
                preserves the original single-symbol behavior exactly.
            
        Returns:
            DataFrame with all strategy columns added
        """
        prev_partition = self._partition_by
        self._partition_by = partition_by
        try:
            if self._use_expandable_mode:
                # Expandable step-based execution
                return self._run_expandable(df)
            else:
                # Legacy 3-step execution (backward compatibility)
                return self._run_legacy(df)
        finally:
            self._partition_by = prev_partition
    
    def _run_legacy(self, df: pl.DataFrame) -> pl.DataFrame:
        """Legacy 3-step execution (backward compatibility)"""
        # Step 1: Setup (Momentum)
        df = self.setup(df)
        
        # Validate setup_valid column exists
        if 'setup_valid' not in df.columns:
            raise ValueError(f"{self.name}: setup() must add 'setup_valid' column")
        
        # Step 2: Trigger (Pattern) - only when setup is valid
        df = self.trigger(df)
        
        # Validate signal column exists
        if 'signal' not in df.columns:
            raise ValueError(f"{self.name}: trigger() must add 'signal' column")
        
        # Step 3: Exit (Management)
        df = self.exit(df)
        
        return df
    
    def _run_expandable(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Execute expandable step-based strategy
        
        Steps are executed sequentially. Each step can specify its own timeframe.
        The executor (in executor.py) handles multi-timeframe alignment.
        """
        if not self.steps:
            raise ValueError(f"{self.name}: No steps defined for expandable mode")
        
        for step in self.steps:
            if not step.enabled:
                continue
            
            # Execute step (subclasses must implement execute_step)
            df = self.execute_step(step, df)
        
        return df
    
    def execute_step(self, step: StepConfig, df: pl.DataFrame) -> pl.DataFrame:
        """
        Execute a single step (for expandable mode)
        
        Subclasses should override this method to implement step logic.
        The step's timeframe is available in step.timeframe.
        
        Args:
            step: StepConfig with step_name, timeframe, and enabled flag
            df: DataFrame with data at the step's timeframe
            
        Returns:
            DataFrame with step results added
        """
        raise NotImplementedError(
            f"{self.name}: execute_step() must be implemented for expandable mode. "
            f"Step: {step.step_name}, Timeframe: {step.timeframe}"
        )
    
    def get_signals(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Extract only the buy/sell signals from the strategy results
        
        Args:
            df: DataFrame after running strategy
            
        Returns:
            DataFrame filtered to only rows with BUY/SELL signals
        """
        return df.filter(
            pl.col('signal').is_in(['BUY', 'SELL'])
        )
    
    def get_latest_signal(self, df: pl.DataFrame) -> Optional[Dict[str, Any]]:
        """
        Get the most recent signal for a symbol
        
        Args:
            df: DataFrame after running strategy
            
        Returns:
            Dictionary with latest signal info or None
        """
        signals = self.get_signals(df)
        if signals.height == 0:
            return None
        
        # Get the most recent signal
        latest = signals.sort('date', descending=True).head(1)
        
        return {
            'symbol': latest['symbol'][0] if 'symbol' in latest.columns else None,
            'date': latest['date'][0],
            'signal': latest['signal'][0],
            'price': latest['close'][0] if 'close' in latest.columns else None,
            'setup_valid': latest['setup_valid'][0],
            'trigger_met': latest['signal'][0] != 'HOLD',
        }
