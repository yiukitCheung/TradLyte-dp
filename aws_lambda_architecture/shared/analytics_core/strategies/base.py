"""
Base Strategy Class - Enforces 3-Step Structure

All strategies must implement:
1. setup() - Momentum/trend validation
2. trigger() - Entry signal detection
3. exit() - Exit/management logic
"""

from abc import ABC, abstractmethod
from typing import Literal, Optional, Dict, Any
import polars as pl


class BaseStrategy(ABC):
    """
    Base class for all trading strategies
    
    Enforces the 3-step sequential logic:
    1. Setup (Momentum): Is the trend valid?
    2. Trigger (Pattern): Did the entry happen?
    3. Exit (Management): When do we sell?
    """
    
    def __init__(self, name: str, description: Optional[str] = None):
        self.name = name
        self.description = description
    
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
    
    def run(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Execute the complete 3-step strategy
        
        Args:
            df: OHLCV dataframe (must have indicators pre-calculated)
            
        Returns:
            DataFrame with all strategy columns added
        """
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
