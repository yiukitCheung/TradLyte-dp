"""
Daily Scanner Engine

Runs pre-built strategies on all active symbols and generates signals.
Used by AWS Batch job for daily scanning.
"""

import polars as pl
from typing import List, Dict, Any, Optional
from datetime import date, datetime
from .models import SignalResult
from .strategies.base import BaseStrategy
from .executor import MultiTimeframeExecutor
from .strategies.library import (
    GoldenCrossStrategy, VegasChannelStrategy
)


class DailyScanner:
    """
    Daily scanner for running strategies on all symbols
    
    Loads active symbols, runs pre-built strategies, and generates signals.
    """
    
    def __init__(self, rds_connection_string: Optional[str] = None):
        """
        Initialize scanner. Data is loaded from RDS (1d); resampled timeframes
        are computed at use from 1d.
        """
        self.executor = MultiTimeframeExecutor(rds_connection_string=rds_connection_string)
    
    def get_active_symbols(self, rds_client) -> List[str]:
        """
        Get list of active symbols from RDS
        
        Args:
            rds_client: RDS client instance (must have get_active_symbols method)
            
        Returns:
            List of active symbol strings
        """
        try:
            symbols = rds_client.get_active_symbols()
            return symbols
        except Exception as e:
            print(f"Error loading active symbols: {str(e)}")
            return []
    
    def get_prebuilt_strategies(self) -> List[BaseStrategy]:
        """
        Get list of pre-built strategies for scanning
        
        Returns:
            List of strategy instances
        """
        strategies = [
            GoldenCrossStrategy(),
            VegasChannelStrategy(),
        ]
        return strategies
    
    def scan_symbol(
        self,
        symbol: str,
        strategy: BaseStrategy,
        scan_date: date,
        rds_client
    ) -> Optional[SignalResult]:
        """
        Run a single strategy on a single symbol
        
        Args:
            symbol: Stock symbol
            strategy: Strategy instance
            scan_date: Date to scan (typically today)
            rds_client: RDS client for data loading
            
        Returns:
            SignalResult if signal generated, None otherwise
        """
        try:
            # Determine required timeframes (simplified - use 1d for now)
            # In practice, strategies could specify their timeframes
            timeframes = ['1d']
            
            # Load data (last 200 days for indicators)
            start_date = date(scan_date.year - 1, scan_date.month, scan_date.day)
            end_date = scan_date
            
            # Execute strategy
            result_df = self.executor.execute_strategy(
                strategy=strategy,
                symbol=symbol,
                timeframes=timeframes,
                start_date=start_date,
                end_date=end_date,
                base_timeframe="1d",
            )
            
            if result_df.height == 0:
                return None
            
            # Get latest signal
            latest_signal = strategy.get_latest_signal(result_df)
            
            if not latest_signal or latest_signal['signal'] == 'HOLD':
                return None
            
            # Create SignalResult
            return SignalResult(
                symbol=symbol,
                date=scan_date.isoformat(),
                signal=latest_signal['signal'],
                price=latest_signal['price'] or 0.0,
                setup_valid=latest_signal.get('setup_valid', False),
                trigger_met=latest_signal.get('trigger_met', False),
                confidence=self._calculate_confidence(latest_signal),
                metadata={
                    'strategy_name': strategy.name,
                    'description': strategy.description
                }
            )
            
        except Exception as e:
            print(f"Error scanning {symbol} with {strategy.name}: {str(e)}")
            return None
    
    def scan_symbols(
        self,
        symbols: List[str],
        strategies: List[BaseStrategy],
        scan_date: date,
        rds_client
    ) -> List[SignalResult]:
        """
        Scan multiple symbols with multiple strategies
        
        Args:
            symbols: List of symbols to scan
            strategies: List of strategies to run
            scan_date: Date to scan
            rds_client: RDS client
            
        Returns:
            List of SignalResult objects
        """
        signals = []
        
        for symbol in symbols:
            for strategy in strategies:
                signal = self.scan_symbol(
                    symbol=symbol,
                    strategy=strategy,
                    scan_date=scan_date,
                    rds_client=rds_client
                )
                
                if signal:
                    signals.append(signal)
        
        return signals
    
    def write_signals_to_rds(
        self,
        signals: List[SignalResult],
        rds_client
    ) -> int:
        """
        Write signals to daily_signals table in RDS
        
        Args:
            signals: List of SignalResult objects
            rds_client: RDS client (must have execute_query method)
            
        Returns:
            Number of signals written
        """
        if not signals:
            return 0
        
        try:
            # Build INSERT query
            values = []
            for signal in signals:
                values.append((
                    signal.symbol,
                    signal.date,
                    signal.metadata.get('strategy_name', 'Unknown'),
                    signal.signal,
                    signal.price,
                    signal.confidence or 0.0,
                    signal.setup_valid,
                    signal.trigger_met,
                    str(signal.metadata) if signal.metadata else '{}'
                ))
            
            # Use INSERT ... ON CONFLICT to handle duplicates
            query = """
            INSERT INTO daily_signals 
            (symbol, date, strategy_name, signal, price, confidence, setup_valid, trigger_met, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (symbol, date, strategy_name) 
            DO UPDATE SET
                signal = EXCLUDED.signal,
                price = EXCLUDED.price,
                confidence = EXCLUDED.confidence,
                setup_valid = EXCLUDED.setup_valid,
                trigger_met = EXCLUDED.trigger_met,
                metadata = EXCLUDED.metadata,
                created_at = CURRENT_TIMESTAMP
            """
            
            # Execute query (assuming rds_client has execute_query method)
            if hasattr(rds_client, 'execute_query'):
                for value_tuple in values:
                    rds_client.execute_query(query, value_tuple)
            else:
                # Fallback: use connection directly
                import psycopg2
                conn = rds_client.conn if hasattr(rds_client, 'conn') else None
                if conn:
                    with conn.cursor() as cur:
                        cur.executemany(query, values)
                    conn.commit()
            
            return len(signals)
            
        except Exception as e:
            print(f"Error writing signals to RDS: {str(e)}")
            raise
    
    def _calculate_confidence(self, signal_data: Dict[str, Any]) -> float:
        """
        Calculate signal confidence score (0.0 to 1.0)
        
        Simple heuristic based on setup_valid and trigger_met
        
        Args:
            signal_data: Dictionary with signal information
            
        Returns:
            Confidence score between 0.0 and 1.0
        """
        confidence = 0.5  # Base confidence
        
        if signal_data.get('setup_valid', False):
            confidence += 0.2
        
        if signal_data.get('trigger_met', False):
            confidence += 0.3
        
        return min(confidence, 1.0)
