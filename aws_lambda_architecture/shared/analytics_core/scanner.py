"""
Daily Scanner Engine

Batch-loads 1d OHLCV from RDS for many symbols at once, resamples to required
timeframes in memory, then runs pick profiles (e.g. Vegas, Golden Cross) and
outputs daily suggested symbols. Used by AWS Batch job for daily scanning.
"""

from typing import List, Dict, Any, Optional, Callable, Union
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
    Daily scanner: batch-load from RDS → resample in memory → run strategies → rank picks.
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
    
    def get_strategy_metadata(
        self,
        include_pick_types: Optional[List[str]] = None,
        exclude_pick_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Return strategy metadata (vegas/golden_cross short/long). Optionally filter by pick_type."""
        strategy_metadata: List[Dict[str, Any]] = [
            {
                "strategy_name": "vegas_channel_short_term",
                "strategy_factory": VegasChannelStrategy,
                "timeframes": ["1d", "3d", "5d"]
            },
      
            {
                "strategy_name": "golden_cross",
                "strategy_factory": GoldenCrossStrategy,
                "timeframes": ["1d", "3d", "5d"]
            }
        ]

        # Optional filtering by pick_type
        if include_pick_types:
            include_set = {p.lower() for p in include_pick_types}
            strategy_metadata = [p for p in strategy_metadata if p["pick_type"].lower() in include_set]
        if exclude_pick_types:
            exclude_set = {p.lower() for p in exclude_pick_types}
            strategy_metadata = [p for p in strategy_metadata if p["pick_type"].lower() not in exclude_set]

        return strategy_metadata

    def _score_signal(
        self,
        symbol: str,
        strategy: BaseStrategy,
        scan_date: date,
        timeframes: List[str],
        symbol_data_dict: Dict[str, Any],
    ) -> Optional[SignalResult]:
        """Score one symbol across timeframes with pre-loaded data; return BUY signal or None."""
        weights = {tf: float(idx + 1) for idx, tf in enumerate(timeframes)} # Weights by position: first=1, second=2, ... (higher timeframe = higher weight).
        weighted_score = 0.0
        weighted_setup = 0.0
        weighted_trigger = 0.0
        weighted_price = 0.0
        total_weight = sum(weights.values())
        votes: Dict[str, Dict[str, Any]] = {}
        # For each timeframe, score the symbolre and calculate the confidence
        for timeframe in timeframes:
            try:
                tf_df = symbol_data_dict.get(timeframe)
                if tf_df is None or (hasattr(tf_df, "height") and tf_df.height == 0):
                    continue
                # Add indicators and patterns to the dataframe
                tf_df = self.executor.prepare_dataframe(tf_df, timeframe)
                # Execute the strategy on the dataframe
                tf_df = strategy.run(tf_df)
                # If the dataframe is empty, continue
                if tf_df.height == 0:
                    continue

                # Get the latest signal from the dataframe
                latest_signal = strategy.get_latest_signal(tf_df)
                
                # If the latest signal is not valid, continue
                if not latest_signal:
                    continue
                # Calculate the weight for the timeframe
                weight = weights[timeframe]
                raw_signal = latest_signal.get("signal", "HOLD")
                setup_valid = bool(latest_signal.get("setup_valid", False))
                trigger_met = bool(latest_signal.get("trigger_met", False))
                price = float(latest_signal.get("price") or 0.0)

                # If the signal is a buy, add the weight to the weighted score
                if raw_signal == "BUY":
                    weighted_score += weight
                # If the signal is a sell, subtract the weight from the weighted score
                elif raw_signal == "SELL":
                    weighted_score -= weight
                # If the setup is valid, add the weight to the weighted setup
                if setup_valid:
                    weighted_setup += weight
                # If the trigger is met and the signal is a buy, add the weight to the weighted trigger
                if trigger_met and raw_signal == "BUY":
                    weighted_trigger += weight
                # If the price is greater than 0, add the weight to the weighted price
                if price > 0:
                    weighted_price += weight * price

                # Add the signal to the votes dictionary
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

        # If the total weight is less than or equal to 0 or the weighted score is less than or equal to 0, return None
        if total_weight <= 0 or weighted_score <= 0:
            return None

        # Calculate the confidence
        confidence = min(max(weighted_score / total_weight, 0.0), 1.0)
        avg_price = (weighted_price / total_weight) if weighted_price > 0 else 0.0
        # Return the signal result
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

    def run(
        self,
        symbols: List[str],
        strategy_metadata: List[Dict[str, Any]],
        scan_date: date,
        include_strategy_names: Optional[List[str]] = None,
        exclude_strategy_names: Optional[List[str]] = None,
        rds_client: Any = None,
        batch_size: int = 100,
    ) -> List[SignalResult]:
        """Batch-load 1d from RDS, resample in memory, run profiles; return list of signals."""
        # Initialize the signals list
        signals: List[SignalResult] = []

        # Filter the pick profiles
        selected_strategy_metadata = strategy_metadata
        if include_strategy_names:
            include_set = {p.lower() for p in include_strategy_names}
            selected_strategy_metadata = [
                p for p in selected_strategy_metadata if p["strategy_name"].lower() in include_set
            ]
        if exclude_strategy_names:
            exclude_set = {p.lower() for p in exclude_strategy_names}
            selected_strategy_metadata = [
                p for p in selected_strategy_metadata if p["strategy_name"].lower() not in exclude_set
            ]

        if not selected_strategy_metadata:
            return signals

        # Get all the timeframes required for the strategy scanning
        all_timeframes: List[str] = []
        for strategy_metadata in selected_strategy_metadata:
            for tf in strategy_metadata.get("timeframes", []):
                if tf not in all_timeframes:
                    all_timeframes.append(tf)

        # Define the start and end dates
        start_date = scan_date - timedelta(days=self.lookback_days)
        end_date = scan_date

        # Step 1: Batch-load the symbols
        for i in range(0, len(symbols), batch_size):
            batch_symbols = symbols[i : i + batch_size]
            # Step 1: Load the data by symbol
            batch_symbols_dict = self.executor.load(
                symbols=batch_symbols,
                timeframes=all_timeframes,
                start_date=start_date,
                end_date=end_date,
            )

            # Step 2: For each symbol, score the signals for each strategy
            for symbol in batch_symbols:
                symbol_data_dict = batch_symbols_dict.get(symbol, {})
                if not symbol_data_dict:
                    continue
                # Step 3: For each profile, score the signals
                for strategy_metadata in selected_strategy_metadata:
                    strategy_factory: Callable[[], BaseStrategy] = strategy_metadata["strategy_factory"]
                    strategy = strategy_factory()
                    timeframes = strategy_metadata.get("timeframes", [])
                    signal = self._score_signal(
                        symbol=symbol,
                        strategy=strategy,
                        scan_date=scan_date,
                        timeframes=timeframes,
                        symbol_data_dict=symbol_data_dict,
                    )
                    # If the signal is valid, add it to the signals list
                    if signal:
                        metadata = dict(signal.metadata or {})
                        metadata["strategy_name"] = strategy_metadata["strategy_name"]
                        metadata["description"] = strategy_metadata["description"]
                        metadata["timeframes"] = strategy_metadata["timeframes"]
                        signal.metadata = metadata
                        signals.append(signal)

        # Return the signals list
        return signals

    def rank(
        self,
        signals: List[SignalResult],
        top_k: int = 10,
        by_pick_type: bool = False,
        unique_symbol: bool = True,
    ) -> Union[List[SignalResult], Dict[str, List[SignalResult]]]:
        """
        Rank signals by confidence + setup/trigger.
        
        Args:
            signals: List of SignalResult to rank.
            top_k: Max signals to return (per pick_type if by_pick_type=True).
            by_pick_type: If True, group by pick_type and return dict; else return flat list.
            unique_symbol: If True, only one signal per symbol.
        
        Returns:
            List[SignalResult] if by_pick_type=False, else Dict[str, List[SignalResult]].
        """
        if not signals:
            return {} if by_pick_type else []

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

        if by_pick_type:
            grouped: Dict[str, List[SignalResult]] = defaultdict(list)
            seen_by_type: Dict[str, set] = defaultdict(set)
            for _, signal in scored:
                pick_type = (signal.metadata or {}).get("pick_type", "unclassified")
                if unique_symbol and signal.symbol in seen_by_type[pick_type]:
                    continue
                if len(grouped[pick_type]) >= top_k:
                    continue
                grouped[pick_type].append(signal)
                seen_by_type[pick_type].add(signal.symbol)
            return dict(grouped)

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

    def write(
        self,
        ranked: Union[List[SignalResult], Dict[str, List[SignalResult]]],
        rds_client: Any,
        scan_date: date,
        all_signals: Optional[List[SignalResult]] = None,
    ) -> int:
        """
        Write signals to RDS.
        
        Args:
            ranked: Output from rank(). List writes to daily_signals; dict writes to daily_scan_top_picks.
            rds_client: RDS client with connection/conn or execute_query().
            scan_date: Date for the scan (used for top_picks table).
            all_signals: If provided (and ranked is dict), also write these to daily_signals.
        
        Returns:
            Total rows written.
        """
        if not ranked and not all_signals:
            return 0

        conn = None
        if hasattr(rds_client, "connection"):
            conn = rds_client.connection
        elif hasattr(rds_client, "conn"):
            conn = rds_client.conn

        total = 0

        if all_signals:
            total += self._write_signals_internal(all_signals, conn, rds_client)

        if isinstance(ranked, dict):
            total += self._write_top_picks_internal(ranked, scan_date, conn, rds_client)
        elif ranked:
            total += self._write_signals_internal(ranked, conn, rds_client)

        return total

    def _write_signals_internal(
        self,
        signals: List[SignalResult],
        conn: Any,
        rds_client: Any,
    ) -> int:
        """Internal: write signals to daily_signals."""
        if not signals:
            return 0

        values = [
            (
                signal.symbol,
                signal.date,
                (signal.metadata or {}).get("strategy_name", "Unknown"),
                signal.signal,
                signal.price,
                signal.confidence or 0.0,
                signal.setup_valid,
                signal.trigger_met,
                str(signal.metadata) if signal.metadata else "{}",
            )
            for signal in signals
        ]

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

        if conn:
            with conn.cursor() as cur:
                cur.executemany(query, values)
            conn.commit()
        elif hasattr(rds_client, "execute_query"):
            for v in values:
                rds_client.execute_query(query, v)
        else:
            raise ValueError("Unsupported rds_client")

        return len(signals)

    def _write_top_picks_internal(
        self,
        ranked: Dict[str, List[SignalResult]],
        scan_date: date,
        conn: Any,
        rds_client: Any,
    ) -> int:
        """Internal: write top picks to daily_scan_top_picks."""
        if not ranked:
            return 0

        values = []
        for pick_type, picks in ranked.items():
            for rank_idx, signal in enumerate(picks, start=1):
                strategy_name = (signal.metadata or {}).get("strategy_name", "Unknown")
                score = float((signal.metadata or {}).get("ranking_score", signal.confidence or 0.0))
                values.append((
                    signal.date or scan_date.isoformat(),
                    pick_type,
                    rank_idx,
                    signal.symbol,
                    strategy_name,
                    signal.signal,
                    signal.price,
                    float(signal.confidence or 0.0),
                    score,
                    str(signal.metadata) if signal.metadata else "{}",
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

        if conn:
            with conn.cursor() as cur:
                cur.executemany(query, values)
            conn.commit()
        elif hasattr(rds_client, "execute_query"):
            for v in values:
                rds_client.execute_query(query, v)
        else:
            raise ValueError("Unsupported rds_client")

        return len(values)
    
    def _calculate_confidence(self, signal_data: Dict[str, Any]) -> float:
        """Confidence 0.5 + 0.2 if setup_valid + 0.3 if trigger_met (cap 1.0)."""
        confidence = 0.5
        if signal_data.get("setup_valid", False):
            confidence += 0.2
        if signal_data.get("trigger_met", False):
            confidence += 0.3
        return min(confidence, 1.0)

    # Backward-compatible alias
    scan = run
