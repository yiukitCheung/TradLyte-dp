"""
Daily Scanner Engine

Batch-loads 1d OHLCV from RDS for many symbols at once, resamples to required
timeframes in memory, then runs pick profiles (e.g. Vegas, Golden Cross) and
outputs daily suggested symbols. Used by AWS Batch job for daily scanning.
"""

from typing import List, Dict, Any, Optional, Callable, Union
from datetime import date, timedelta
from collections import defaultdict
import json
import polars as pl
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
        higher_tf_buy_lookback_candles = 5

        def _timeframe_to_days(tf: str) -> int:
            tf_str = str(tf).strip().lower()
            if tf_str.endswith("d"):
                numeric = tf_str[:-1].strip()
                if numeric.isdigit():
                    return int(numeric)
            return 10**9

        ordered_timeframes = sorted(timeframes, key=_timeframe_to_days)
        anchor_timeframe = ordered_timeframes[0] if ordered_timeframes else None
        if not anchor_timeframe:
            return None

        weights = {tf: float(idx + 1) for idx, tf in enumerate(timeframes)}
        print(f"Score Signal: Weights per timeframe: {weights}")
        weighted_score = 0.0
        weighted_setup = 0.0
        weighted_trigger = 0.0
        total_weight = sum(weights.values())
        votes: Dict[str, Dict[str, Any]] = {}
        selected_price: Optional[float] = None
        prepared_by_timeframe: Dict[str, pl.DataFrame] = {}

        # Run strategy per timeframe first.
        for timeframe in timeframes:
            try:
                tf_df = symbol_data_dict.get(timeframe)
                if tf_df is None or (hasattr(tf_df, "height") and tf_df.height == 0):
                    print(f"Score Signal: No data for timeframe '{timeframe}'.")
                    votes[timeframe] = {"signal": "NO_DATA", "weight": weights[timeframe]}
                    continue
                tf_df = self.executor.prepare_dataframe(tf_df, timeframe)
                # Run Strategy
                tf_df = strategy.run(tf_df)
                # Get Signals
                tf_df = strategy.get_signals(tf_df)
                if tf_df.height == 0:
                    votes[timeframe] = {"signal": "NO_DATA", "weight": weights[timeframe]}
                    continue
                prepared_by_timeframe[timeframe] = tf_df
                
            except Exception as timeframe_error:
                votes[timeframe] = {"error": str(timeframe_error), "weight": weights[timeframe]}
                continue
        # 1) Anchor rule: the lowest timeframe must be BUY exactly on scan_date.
        anchor_df = prepared_by_timeframe.get(anchor_timeframe)
        if anchor_df is None or anchor_df.height == 0:
            print(f"Score Signal: No data for anchor timeframe '{anchor_timeframe}' on scan date.")
            votes[anchor_timeframe] = {"signal": "NO_DATA_ON_SCAN_DATE", "weight": weights.get(anchor_timeframe, 0.0)}
            return None
        anchor_scan_day_df = anchor_df.filter(pl.col("date") == scan_date)
        if anchor_scan_day_df.height == 0:
            print(f"Score Signal: No row for anchor timeframe '{anchor_timeframe}' matching scan date.")
            votes[anchor_timeframe] = {"signal": "NO_DATA_ON_SCAN_DATE", "weight": weights.get(anchor_timeframe, 0.0)}
            return None
        anchor_row = anchor_scan_day_df.sort("date", descending=True).head(1)
        anchor_signal = anchor_row["signal"][0] if "signal" in anchor_row.columns else "HOLD"
        if anchor_signal != "BUY":
            print(f"Score Signal: Anchor timeframe '{anchor_timeframe}' signal is not BUY ({anchor_signal}), returning None.")
            votes[anchor_timeframe] = {"signal": str(anchor_signal), "weight": weights.get(anchor_timeframe, 0.0)}
            return None

        print(f"Score Signal: Anchor timeframe '{anchor_timeframe}' confirmed as BUY.")
        anchor_weight = weights.get(anchor_timeframe, 0.0)
        weighted_score += anchor_weight
        weighted_trigger += anchor_weight
        anchor_setup_valid = False
        if "setup_valid" in anchor_row.columns:
            setup_value = anchor_row["setup_valid"][0]
            anchor_setup_valid = bool(setup_value) if setup_value is not None else False
        if anchor_setup_valid:
            weighted_setup += anchor_weight
        if "close" in anchor_row.columns:
            close_value = anchor_row["close"][0]
            if close_value is not None:
                selected_price = float(close_value)
        votes[anchor_timeframe] = {
            "signal": "BUY",
            "weight": anchor_weight,
            "match_mode": "exact_scan_date",
            "matched_date": str(scan_date),
        }

        # 2) Higher timeframe rule: count BUY if found within the last N candles up to anchor day.
        for timeframe in timeframes:
            if timeframe == anchor_timeframe:
                continue
            tf_df = prepared_by_timeframe.get(timeframe)
            if tf_df is None or tf_df.height == 0:
                print(f"Score Signal: No data for higher timeframe '{timeframe}'.")
                votes[timeframe] = {"signal": "NO_DATA", "weight": weights[timeframe]}
                continue

            candles_up_to_anchor = tf_df.filter(pl.col("date") <= scan_date).sort("date", descending=True)
            if candles_up_to_anchor.height == 0:
                print(f"Score Signal: No candles found up to anchor for timeframe '{timeframe}'.")
                votes[timeframe] = {"signal": "NO_DATA_UP_TO_SCAN_DATE", "weight": weights[timeframe]}
                continue

            recent_window = candles_up_to_anchor.head(higher_tf_buy_lookback_candles)
            buy_rows = recent_window.filter(pl.col("signal") == "BUY")
            weight = weights[timeframe]

            if buy_rows.height > 0:
                matched_buy = buy_rows.sort("date", descending=True).head(1)
                print(f"Score Signal: BUY found in higher timeframe '{timeframe}' in last {higher_tf_buy_lookback_candles} candles on {matched_buy['date'][0]}.")
                weighted_score += weight
                weighted_trigger += weight

                setup_valid = False
                if "setup_valid" in matched_buy.columns:
                    setup_value = matched_buy["setup_valid"][0]
                    setup_valid = bool(setup_value) if setup_value is not None else False
                if setup_valid:
                    print(f"Score Signal: Higher timeframe '{timeframe}' setup is valid.")
                    weighted_setup += weight

                votes[timeframe] = {
                    "signal": "BUY",
                    "weight": weight,
                    "match_mode": "buy_within_lookback_candles",
                    "lookback_candles": higher_tf_buy_lookback_candles,
                    "matched_date": str(matched_buy["date"][0]),
                }
            else:
                print(f"Score Signal: No BUY signals found in higher timeframe '{timeframe}' in last {higher_tf_buy_lookback_candles} candles.")
                votes[timeframe] = {
                    "signal": "HOLD",
                    "weight": weight,
                    "match_mode": "buy_within_lookback_candles",
                    "lookback_candles": higher_tf_buy_lookback_candles,
                }

        if total_weight <= 0 or weighted_score <= 0:
            print(f"Score Signal: Weighted score or total weight <= 0 (weighted_score={weighted_score}, total_weight={total_weight}), returning None.")
            return None

        confidence = min(max(weighted_score / total_weight, 0.0), 1.0)
        print(f"Score Signal: Final weighted_score={weighted_score}, total_weight={total_weight}, confidence={confidence}")
        return SignalResult(
            symbol=symbol,
            date=scan_date.isoformat(),
            signal="BUY",
            price=selected_price if selected_price is not None else 0.0,
            setup_valid=(weighted_setup > 0),
            trigger_met=(weighted_trigger > 0),
            confidence=confidence,
            metadata={
                "strategy_name": strategy.name,
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
        print(f"Starting run: {len(symbols)} symbols, scan_date={scan_date}")

        # Filter the pick profiles
        selected_strategy_metadata = strategy_metadata
        if include_strategy_names:
            include_set = {p.lower() for p in include_strategy_names}
            print(f"Including strategies: {', '.join(include_set)}")
            selected_strategy_metadata = [
                p for p in selected_strategy_metadata if p["strategy_name"].lower() in include_set
            ]
        if exclude_strategy_names:
            exclude_set = {p.lower() for p in exclude_strategy_names}
            print(f"Excluding strategies: {', '.join(exclude_set)}")
            selected_strategy_metadata = [
                p for p in selected_strategy_metadata if p["strategy_name"].lower() not in exclude_set
            ]

        if not selected_strategy_metadata:
            print("No strategies selected after filtering, returning empty signals list.")
            return signals

        # Get all the timeframes required for the strategy scanning
        all_timeframes: List[str] = []
        for strategy_metadata in selected_strategy_metadata:
            for tf in strategy_metadata.get("timeframes", []):
                if tf not in all_timeframes:
                    all_timeframes.append(tf)
        print(f"All timeframes required: {', '.join(all_timeframes)}")

        # Define the start and end dates
        start_date = scan_date - timedelta(days=365 * 3)
        end_date = scan_date
        print(f"Data window: {start_date} to {end_date}")

        # Step 1: Batch-load the symbols
        for i in range(0, len(symbols), batch_size):
            batch_symbols = symbols[i : i + batch_size]
            print(f"Processing batch {i}-{min(i+batch_size, len(symbols))} ({len(batch_symbols)} symbols)")
            # Step 1: Load the data by symbol
            batch_symbols_dict = self.executor.load(
                symbols=batch_symbols,
                timeframes=all_timeframes,
                start_date=start_date,
                end_date=end_date,
            )
            print(f"Loaded symbol data for {sum(1 for s in batch_symbols if s in batch_symbols_dict)}/{len(batch_symbols)} batch symbols.")

            # Step 2: For each symbol, score the signals for each strategy
            for symbol in batch_symbols:
                symbol_data_dict = batch_symbols_dict.get(symbol, {})
                if not symbol_data_dict:
                    print(f"No data found for symbol: {symbol}")
                    continue
                # Step 3: For each profile, score the signals
                for strategy_metadata in selected_strategy_metadata:
                    strategy_factory: Callable[[], BaseStrategy] = strategy_metadata["strategy_factory"]
                    strategy = strategy_factory()
                    timeframes = strategy_metadata.get("timeframes", [])
                    print(f"Scoring {symbol} for strategy {strategy_metadata['strategy_name']} on tfs {timeframes}")
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
                        metadata["timeframes"] = strategy_metadata["timeframes"]
                        signal.metadata = metadata
                        signals.append(signal)
                        print(f"Added signal for symbol: {symbol}, strategy: {strategy_metadata['strategy_name']}")

        print(f"Run complete. Total signals generated: {len(signals)}")
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
        Rank signals by confidence only (dense rank).
        
        Args:
            signals: List of SignalResult to rank.
            top_k: Maximum dense-rank bucket to include (per strategy_name if by_pick_type=True).
            by_pick_type: If True, group by strategy_name and return dict; else return flat list.
            unique_symbol: If True, only one signal per symbol.
        
        Returns:
            List[SignalResult] if by_pick_type=False, else Dict[str, List[SignalResult]].
        """
        if not signals:
            return {} if by_pick_type else []

        def rank_dense_confidence(items: List[SignalResult]) -> List[SignalResult]:
            scored: List[tuple[float, SignalResult]] = [
                (float(signal.confidence or 0.0), signal) for signal in items
            ]
            scored.sort(key=lambda x: x[0], reverse=True)

            ranked_items: List[SignalResult] = []
            seen_symbols: set[str] = set()
            current_rank = 0
            previous_confidence: Optional[float] = None
            for confidence, signal in scored:
                if previous_confidence is None or confidence != previous_confidence:
                    current_rank += 1
                    previous_confidence = confidence
                if current_rank > top_k:
                    break
                if unique_symbol and signal.symbol in seen_symbols:
                    continue
                metadata = dict(signal.metadata or {})
                metadata["ranking_score"] = confidence
                metadata["dense_rank"] = current_rank
                signal.metadata = metadata
                ranked_items.append(signal)
                seen_symbols.add(signal.symbol)
            return ranked_items

        if by_pick_type:
            grouped_input: Dict[str, List[SignalResult]] = defaultdict(list)
            for signal in signals:
                pick_type = (signal.metadata or {}).get("strategy_name", "unclassified")
                grouped_input[pick_type].append(signal)
            return {
                pick_type: rank_dense_confidence(group_signals)
                for pick_type, group_signals in grouped_input.items()
            }

        return rank_dense_confidence(signals)

    def write(
        self,
        ranked: Union[List[SignalResult], Dict[str, List[SignalResult]]],
        rds_client: Any,
        scan_date: date,
    ) -> int:
        """
        Write ranked top picks to RDS.
        
        Args:
            ranked: Output from rank(). Dict writes grouped pick types;
                list is written as pick_type='unclassified'.
            rds_client: RDS client with connection/conn or execute_query().
            scan_date: Date for the scan (used for top_picks table).
        
        Returns:
            Total rows written.
        """
        if not ranked:
            return 0

        conn = None
        if hasattr(rds_client, "connection"):
            conn = rds_client.connection
        elif hasattr(rds_client, "conn"):
            conn = rds_client.conn

        if isinstance(ranked, dict):
            ranked_dict = ranked
        else:
            ranked_dict = {"unclassified": ranked}

        return self._write_top_picks_internal(ranked_dict, scan_date, conn, rds_client)

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
        for strategy_name, picks in ranked.items():
            for rank_idx, signal in enumerate(picks, start=1):
                metadata_json = json.dumps(signal.metadata or {})
                values.append((
                    signal.date or scan_date.isoformat(),
                    signal.symbol,
                    strategy_name,
                    signal.signal,
                    signal.price,
                    float(signal.confidence or 0.0),
                    metadata_json,
                    rank_idx
                ))

        query = """
        INSERT INTO stock_picks
        (scan_date, symbol, strategy_name, signal, price, confidence, metadata, rank)
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (scan_date, symbol, strategy_name)
        DO UPDATE SET   
            signal = EXCLUDED.signal,
            price = EXCLUDED.price,
            confidence = EXCLUDED.confidence,
            metadata = EXCLUDED.metadata,
            rank = EXCLUDED.rank
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
    
    # Backward-compatible alias
    scan = run
