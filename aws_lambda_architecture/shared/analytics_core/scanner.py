"""
Daily Scanner Engine

Runs pre-built strategies on all active symbols and generates signals.
Used by AWS Batch job for daily scanning.
"""

from typing import List, Dict, Any, Optional, Callable
from datetime import date, timedelta
from collections import defaultdict
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
    
    def __init__(
        self,
        rds_connection_string: Optional[str] = None,
        s3_bucket: Optional[str] = None,
        use_s3: bool = False,
        lookback_days: int = 365
    ):
        """
        Initialize scanner. Data is loaded from RDS (1d); resampled timeframes
        are computed at use from 1d.
        """
        # Kept for backward compatibility with existing callers.
        self.s3_bucket = s3_bucket
        self.use_s3 = use_s3
        self.lookback_days = lookback_days
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

    def get_pick_profiles(self) -> List[Dict[str, Any]]:
        """
        Active pick profiles (touch strategy intentionally excluded for now):
        - vegas short-term / long-term
        - golden-cross short-term / long-term
        """
        return [
            {
                "pick_type": "vegas_short_term",
                "strategy_factory": VegasChannelStrategy,
                "strategy_name": "Vegas Channel",
                "timeframes": ["1d", "3d", "5d"],
                "term": "short",
            },
            {
                "pick_type": "vegas_long_term",
                "strategy_factory": VegasChannelStrategy,
                "strategy_name": "Vegas Channel",
                "timeframes": ["8d", "13d", "21d", "34d"],
                "term": "long",
            },
            {
                "pick_type": "golden_cross_short_term",
                "strategy_factory": GoldenCrossStrategy,
                "strategy_name": "Golden Cross",
                "timeframes": ["1d", "3d", "5d"],
                "term": "short",
            },
            {
                "pick_type": "golden_cross_long_term",
                "strategy_factory": GoldenCrossStrategy,
                "strategy_name": "Golden Cross",
                "timeframes": ["8d", "13d", "21d", "34d"],
                "term": "long",
            },
        ]

    def _build_term_weights(self, timeframes: List[str]) -> Dict[str, float]:
        """
        Rank-based weights: later/higher timeframe in each term gets higher weight.
        Example short-term ['1d','3d','5d'] => {1d:1, 3d:2, 5d:3}
        """
        return {tf: float(idx + 1) for idx, tf in enumerate(timeframes)}

    def _score_multi_timeframe_signal(
        self,
        symbol: str,
        strategy: BaseStrategy,
        scan_date: date,
        timeframes: List[str]
    ) -> Optional[SignalResult]:
        """
        Run one strategy over multiple timeframes and combine into one weighted signal.

        Note:
        - Touch-related logic is intentionally ignored for now (placeholder for later).
        - Only BUY-side picks are returned for scanner output.
        """
        start_date = scan_date - timedelta(days=self.lookback_days)
        end_date = scan_date
        weights = self._build_term_weights(timeframes)
        weighted_score = 0.0
        weighted_setup = 0.0
        weighted_trigger = 0.0
        weighted_price = 0.0
        total_weight = sum(weights.values())
        votes: Dict[str, Dict[str, Any]] = {}

        for timeframe in timeframes:
            try:
                result_df = self.executor.execute_strategy(
                    strategy=strategy,
                    symbol=symbol,
                    timeframes=[timeframe],
                    start_date=start_date,
                    end_date=end_date,
                    base_timeframe=timeframe,
                )
                if result_df.height == 0:
                    continue
                latest_signal = strategy.get_latest_signal(result_df)
                if not latest_signal:
                    continue

                weight = weights[timeframe]
                raw_signal = latest_signal.get("signal", "HOLD")
                setup_valid = bool(latest_signal.get("setup_valid", False))
                trigger_met = bool(latest_signal.get("trigger_met", False))
                price = float(latest_signal.get("price") or 0.0)

                # BUY contributes positive score; SELL contributes negative score.
                if raw_signal == "BUY":
                    weighted_score += weight
                elif raw_signal == "SELL":
                    weighted_score -= weight

                if setup_valid:
                    weighted_setup += weight
                if trigger_met and raw_signal == "BUY":
                    weighted_trigger += weight
                if price > 0:
                    weighted_price += weight * price

                votes[timeframe] = {
                    "signal": raw_signal,
                    "setup_valid": setup_valid,
                    "trigger_met": trigger_met,
                    "price": price,
                    "weight": weight,
                }
            except Exception as timeframe_error:
                votes[timeframe] = {"error": str(timeframe_error), "weight": weights[timeframe]}
                continue

        if total_weight <= 0:
            return None

        # Keep scanner output focused on long picks only (BUY bias).
        if weighted_score <= 0:
            return None

        confidence = min(max(weighted_score / total_weight, 0.0), 1.0)
        avg_price = (weighted_price / total_weight) if weighted_price > 0 else 0.0

        return SignalResult(
            symbol=symbol,
            date=scan_date.isoformat(),
            signal="BUY",
            price=avg_price,
            setup_valid=(weighted_setup > 0),
            trigger_met=(weighted_trigger > 0),
            confidence=confidence,
            metadata={
                "strategy_name": strategy.name,
                "description": strategy.description,
                "timeframes": timeframes,
                "timeframe_votes": votes,
                "weighted_score": weighted_score,
                "total_weight": total_weight,
            },
        )

    def get_required_timeframes(self, strategy: BaseStrategy, base_timeframe: str = "1d") -> List[str]:
        """
        Infer required timeframes from strategy definition.

        - Expandable strategies: union of enabled step timeframes + base timeframe
        - Legacy strategies: use `required_timeframes` if present, otherwise base timeframe only
        """
        timeframes = {base_timeframe}

        if getattr(strategy, "_use_expandable_mode", False) and getattr(strategy, "steps", None):
            for step in strategy.steps:
                if getattr(step, "enabled", True):
                    timeframes.add(step.timeframe)
        elif hasattr(strategy, "required_timeframes"):
            configured = getattr(strategy, "required_timeframes") or []
            for tf in configured:
                timeframes.add(str(tf))

        return sorted(timeframes)
    
    def scan_symbol(
        self,
        symbol: str,
        strategy: BaseStrategy,
        scan_date: date,
        rds_client,
        base_timeframe: str = "1d"
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
            timeframes = self.get_required_timeframes(strategy, base_timeframe=base_timeframe)
            
            # Load data (last 200 days for indicators)
            start_date = scan_date - timedelta(days=self.lookback_days)
            end_date = scan_date
            
            # Execute strategy
            result_df = self.executor.execute_strategy(
                strategy=strategy,
                symbol=symbol,
                timeframes=timeframes,
                start_date=start_date,
                end_date=end_date,
                base_timeframe=base_timeframe,
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
                    'description': strategy.description,
                    'timeframes': timeframes,
                    'base_timeframe': base_timeframe
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
        rds_client,
        base_timeframe: str = "1d"
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
                    rds_client=rds_client,
                    base_timeframe=base_timeframe
                )
                
                if signal:
                    signals.append(signal)
        
        return signals

    def scan_symbols_by_pick_profiles(
        self,
        symbols: List[str],
        pick_profiles: List[Dict[str, Any]],
        scan_date: date,
        rds_client,
    ) -> List[SignalResult]:
        """
        Scan symbols using the 4 pick profiles and return aggregated pick signals.
        """
        signals: List[SignalResult] = []

        for symbol in symbols:
            for profile in pick_profiles:
                strategy_factory: Callable[[], BaseStrategy] = profile["strategy_factory"]
                strategy = strategy_factory()
                signal = self._score_multi_timeframe_signal(
                    symbol=symbol,
                    strategy=strategy,
                    scan_date=scan_date,
                    timeframes=profile["timeframes"],
                )
                if signal:
                    metadata = dict(signal.metadata or {})
                    metadata["pick_type"] = profile["pick_type"]
                    metadata["term"] = profile["term"]
                    signal.metadata = metadata
                    signals.append(signal)

        return signals

    def rank_signals(
        self,
        signals: List[SignalResult],
        top_k: int = 10,
        unique_symbol: bool = True
    ) -> List[SignalResult]:
        """
        Rank signals and return top picks.

        Default score (adjustable when your explicit criteria is ready):
        score = confidence + 0.15*setup_valid + 0.15*trigger_met + 0.1*(signal == BUY)
        """
        if not signals:
            return []

        scored: List[tuple[float, SignalResult]] = []
        for signal in signals:
            confidence = float(signal.confidence or 0.0)
            score = confidence
            if signal.setup_valid:
                score += 0.15
            if signal.trigger_met:
                score += 0.15
            if signal.signal == "BUY":
                score += 0.10

            metadata = dict(signal.metadata or {})
            metadata["ranking_score"] = min(score, 1.5)
            signal.metadata = metadata
            scored.append((score, signal))

        scored.sort(key=lambda x: x[0], reverse=True)

        ranked: List[SignalResult] = []
        seen_symbols: set[str] = set()
        for _, signal in scored:
            if unique_symbol and signal.symbol in seen_symbols:
                continue
            ranked.append(signal)
            seen_symbols.add(signal.symbol)
            if len(ranked) >= top_k:
                break

        return ranked

    def rank_signals_by_pick_type(
        self,
        signals: List[SignalResult],
        top_k_per_pick_type: int = 10,
        unique_symbol: bool = True,
    ) -> Dict[str, List[SignalResult]]:
        """
        Rank picks independently per pick_type (e.g. vegas_short_term).
        """
        grouped: Dict[str, List[SignalResult]] = defaultdict(list)
        for signal in signals:
            pick_type = (signal.metadata or {}).get("pick_type", "unclassified")
            grouped[pick_type].append(signal)

        ranked_by_group: Dict[str, List[SignalResult]] = {}
        for pick_type, group_signals in grouped.items():
            ranked_by_group[pick_type] = self.rank_signals(
                signals=group_signals,
                top_k=top_k_per_pick_type,
                unique_symbol=unique_symbol,
            )

        return ranked_by_group
    
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
            values = [
                (
                    signal.symbol,
                    signal.date,
                    signal.metadata.get('strategy_name', 'Unknown'),
                    signal.signal,
                    signal.price,
                    signal.confidence or 0.0,
                    signal.setup_valid,
                    signal.trigger_met,
                    str(signal.metadata) if signal.metadata else '{}'
                )
                for signal in signals
            ]
            
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
            
            # Prefer bulk execution via connection when available.
            conn = None
            if hasattr(rds_client, 'connection'):
                conn = rds_client.connection
            elif hasattr(rds_client, 'conn'):
                conn = rds_client.conn

            if conn:
                with conn.cursor() as cur:
                    cur.executemany(query, values)
                conn.commit()
            elif hasattr(rds_client, 'execute_query'):
                for value_tuple in values:
                    rds_client.execute_query(query, value_tuple)
            else:
                raise ValueError("Unsupported rds_client. Expected connection/conn or execute_query()")
            
            return len(signals)
            
        except Exception as e:
            print(f"Error writing signals to RDS: {str(e)}")
            raise

    def write_top_picks_to_rds(
        self,
        top_picks: Any,
        scan_date: date,
        rds_client
    ) -> int:
        """
        Persist top picks into `daily_scan_top_picks` for app consumption.
        """
        if not top_picks:
            return 0

        try:
            if isinstance(top_picks, dict):
                items: List[tuple[str, SignalResult]] = []
                for pick_type, picks in top_picks.items():
                    for signal in picks:
                        items.append((pick_type, signal))
            else:
                items = [((signal.metadata or {}).get("pick_type", "unclassified"), signal) for signal in top_picks]

            values = []
            rank_map: Dict[str, int] = defaultdict(int)
            for pick_type, signal in items:
                rank_map[pick_type] += 1
                rank = rank_map[pick_type]
                strategy_name = signal.metadata.get("strategy_name", "Unknown")
                score = float((signal.metadata or {}).get("ranking_score", signal.confidence or 0.0))
                values.append((
                    signal.date or scan_date.isoformat(),
                    pick_type,
                    rank,
                    signal.symbol,
                    strategy_name,
                    signal.signal,
                    signal.price,
                    float(signal.confidence or 0.0),
                    score,
                    str(signal.metadata) if signal.metadata else "{}"
                ))

            query = """
            INSERT INTO daily_scan_top_picks
            (scan_date, pick_type, rank, symbol, strategy_name, signal, price, confidence, score, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (scan_date, pick_type, rank)
            DO UPDATE SET
                symbol = EXCLUDED.symbol,
                strategy_name = EXCLUDED.strategy_name,
                signal = EXCLUDED.signal,
                price = EXCLUDED.price,
                confidence = EXCLUDED.confidence,
                score = EXCLUDED.score,
                metadata = EXCLUDED.metadata,
                created_at = CURRENT_TIMESTAMP
            """

            conn = None
            if hasattr(rds_client, "connection"):
                conn = rds_client.connection
            elif hasattr(rds_client, "conn"):
                conn = rds_client.conn

            if conn:
                with conn.cursor() as cur:
                    cur.executemany(query, values)
                conn.commit()
            elif hasattr(rds_client, "execute_query"):
                for value_tuple in values:
                    rds_client.execute_query(query, value_tuple)
            else:
                raise ValueError("Unsupported rds_client. Expected connection/conn or execute_query()")

            return len(items)
        except Exception as e:
            print(f"Error writing top picks to RDS: {str(e)}")
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
