"""
Full-Universe Scanner

Runs a strategy across the ENTIRE symbol universe in a single Polars pass,
instead of looping one symbol at a time. The input is the consolidated
long-format snapshot produced by the snapshot_builder Lambda:

    [date: Date, symbol: Utf8, open, high, low, close, volume: Float64]

One strategy, two evaluation modes
----------------------------------
This module does NOT re-implement strategy logic. It runs the SAME shared
library strategies the backtester uses (``strategies.library.*``), but
evaluates them with ``strategy.run(frame, partition_by="symbol")``.
``BaseStrategy`` is partition-aware: every cross-row op a strategy performs
(``rolling_*``, ``shift``, ``ewm_mean``, ``diff``, aggregates) is wrapped in
``self._w(...)``, which expands to ``.over("symbol")`` here and to a no-op on
the single-symbol backtester path. This keeps a single source of truth for
signal logic while scanning the whole universe in one pass.

Why this is fast
----------------
Polars window functions (``.over("symbol")``) compute per-symbol rolling
indicators and shifts across all symbols at once, using a single vectorized
engine pass with no Python-level loop. A 10k-symbol x 5y frame (~12M rows)
fits comfortably in RAM and scans in well under a second per strategy.

CRITICAL correctness rule
--------------------------
Without per-symbol partitioning, one symbol's tail bleeds into the next
symbol's head (e.g. AAPL's last 49 closes contaminate AAL's first SMA-50),
silently corrupting signals. The frame MUST be sorted by ``[symbol, date]``
before evaluation; :func:`run_strategy_universe` enforces this.

Timeframes
----------
The snapshot is 1d. Longer timeframes (e.g. 3d "long-term") are derived on the
fly via :func:`resample_long` — N consecutive trading rows aggregated per
symbol — and never persisted.
"""

from __future__ import annotations

import polars as pl

from .strategies.library import GoldenCrossStrategy, VegasChannelStrategy

SNAPSHOT_COLUMNS = ["date", "symbol", "open", "high", "low", "close", "volume"]


# ---------------------------------------------------------------------------
# Timeframe resampling (1d base → N trading-day bars)
# ---------------------------------------------------------------------------

def resample_long(df: pl.DataFrame, n_days: int) -> pl.DataFrame:
    """
    Resample a long-format 1d frame to N-calendar-day bars, per symbol.

    PARITY: this must match ``analytics_core.inputs.resample_ohlcv``, which the
    backtester uses. That function calls
    ``group_by_dynamic(time_col, every="Nd")`` (calendar windows anchored to a
    fixed epoch origin, left-labeled — the bar's ``date`` is the window START),
    once per symbol. We reproduce it in a single vectorized pass with
    ``group_by="symbol"``. Using calendar windows (not row buckets) is essential:
    row bucketing drifts whenever a symbol has gaps/holidays, which would make
    the resampled bars — and therefore the signals — diverge from production.
    """
    if n_days <= 1:
        return df.sort(["symbol", "date"]).select(SNAPSHOT_COLUMNS)

    grouped = (
        df.sort(["symbol", "date"])
        .group_by_dynamic(
            "date",
            every=f"{n_days}d",
            group_by="symbol",
        )
        .agg(
            pl.col("open").first().alias("open"),
            pl.col("high").max().alias("high"),
            pl.col("low").min().alias("low"),
            pl.col("close").last().alias("close"),
            pl.col("volume").sum().alias("volume"),
        )
        .sort(["symbol", "date"])
    )
    return grouped.select(SNAPSHOT_COLUMNS)


# ---------------------------------------------------------------------------
# Strategy registry + multi-timeframe scoring
# ---------------------------------------------------------------------------

# strategy_name -> (strategy_factory, timeframes). The factory builds a
# BaseStrategy from the shared strategy library; we run it ONCE over the entire
# universe with ``run(frame, partition_by="symbol")`` so every cross-row op the
# strategy performs is scoped per symbol. This is the single source of truth for
# strategy logic — it is the *same* class the backtester uses, just evaluated
# partition-aware.
STRATEGY_REGISTRY = {
    "golden_cross": (GoldenCrossStrategy, ["1d", "3d", "5d"]),
    "vegas_channel_short_term": (VegasChannelStrategy, ["1d", "3d", "5d"]),
}


def run_strategy_universe(factory, frame: pl.DataFrame, timeframe: str) -> pl.DataFrame:
    """
    Run a library strategy across the whole universe for one timeframe frame.

    Builds a fresh strategy instance, injects the ``timeframe`` column some
    strategies key off (e.g. Vegas' observation window), sorts by
    ``[symbol, date]`` so windows are well-defined, and evaluates the strategy
    with ``partition_by="symbol"``. Returns the strategy's full output frame
    (includes at least ``signal`` and ``setup_valid``).
    """
    f = frame.sort(["symbol", "date"])
    if "timeframe" not in f.columns:
        f = f.with_columns(pl.lit(timeframe).alias("timeframe"))
    return factory().run(f, partition_by="symbol")

_HIGHER_TF_LOOKBACK = 5  # most-recent N higher-tf signal rows considered on/before scan_date


def _timeframe_days(tf: str) -> int:
    tf = str(tf).strip().lower()
    return int(tf[:-1]) if tf.endswith("d") and tf[:-1].isdigit() else 10**9


def score_multi_timeframe(
    base_1d: pl.DataFrame,
    strategy_name: str,
    scan_date,
) -> pl.DataFrame:
    """
    Multi-timeframe scoring for the whole universe.

    Rules:
      * weights per timeframe = position+1 (1d=1, 3d=2, 5d=3); total = sum.
      * ANCHOR (lowest tf, 1d): must emit BUY exactly on scan_date, else the
        symbol is dropped. Its weight always counts toward the score.
      * HIGHER tfs: count their weight if a BUY appears within the last
        ``_HIGHER_TF_LOOKBACK`` signal rows on/before scan_date.
      * confidence = weighted_score / total_weight (clamped 0..1).
      * setup_valid (output) = weighted_setup > 0; trigger_met = True.

    Returns one row per qualifying symbol:
      [symbol, signal='BUY', price, confidence, setup_valid, trigger_met, strategy_name]
    """
    factory, timeframes = STRATEGY_REGISTRY[strategy_name]
    ordered = sorted(timeframes, key=_timeframe_days)
    anchor_tf = ordered[0]
    weights = {tf: float(i + 1) for i, tf in enumerate(timeframes)}
    total_weight = sum(weights.values())

    # Run the strategy per timeframe over the full universe.
    sig_by_tf: dict = {}
    for tf in timeframes:
        frame = base_1d if tf == "1d" else resample_long(base_1d, _timeframe_days(tf))
        sig_by_tf[tf] = run_strategy_universe(factory, frame, tf)

    # ---- anchor: BUY exactly on scan_date ----
    anchor = (
        sig_by_tf[anchor_tf]
        .filter((pl.col("date") == scan_date) & (pl.col("signal") == "BUY"))
        .select(
            "symbol",
            pl.col("close").alias("price"),
            pl.col("setup_valid").fill_null(False).alias("_anchor_setup"),
        )
        .unique(subset=["symbol"], keep="last")
    )
    if anchor.height == 0:
        return _empty_score_frame()

    aw = weights[anchor_tf]
    out = anchor.with_columns(
        [
            pl.lit(aw).alias("_wscore"),
            (pl.when(pl.col("_anchor_setup")).then(aw).otherwise(0.0)).alias("_wsetup"),
        ]
    ).drop("_anchor_setup")

    # ---- higher timeframes: BUY within last N signal rows up to scan_date ----
    for tf in timeframes:
        if tf == anchor_tf:
            continue
        w = weights[tf]
        sig = (
            sig_by_tf[tf]
            .filter(pl.col("signal").is_in(["BUY", "SELL"]) & (pl.col("date") <= scan_date))
            .sort(["symbol", "date"], descending=[False, True])
            .group_by("symbol")
            .head(_HIGHER_TF_LOOKBACK)
        )
        # Most-recent BUY per symbol within the lookback window (if any).
        buys = (
            sig.filter(pl.col("signal") == "BUY")
            .sort(["symbol", "date"], descending=[False, True])
            .group_by("symbol")
            .agg(pl.col("setup_valid").fill_null(False).first().alias(f"_setup_{tf}"))
            .with_columns(pl.lit(True).alias(f"_has_{tf}"))
        )
        out = out.join(buys, on="symbol", how="left")
        out = out.with_columns(
            [
                (pl.col("_wscore") + pl.when(pl.col(f"_has_{tf}").fill_null(False)).then(w).otherwise(0.0)).alias("_wscore"),
                (pl.col("_wsetup") + pl.when(pl.col(f"_setup_{tf}").fill_null(False)).then(w).otherwise(0.0)).alias("_wsetup"),
            ]
        ).drop([f"_has_{tf}", f"_setup_{tf}"])

    out = out.with_columns(
        [
            pl.lit("BUY").alias("signal"),
            (pl.col("_wscore") / total_weight).clip(0.0, 1.0).alias("confidence"),
            (pl.col("_wsetup") > 0).alias("setup_valid"),
            pl.lit(True).alias("trigger_met"),
            pl.lit(strategy_name).alias("strategy_name"),
        ]
    )
    return out.select(
        "symbol", "signal", "price", "confidence", "setup_valid", "trigger_met", "strategy_name"
    ).sort("confidence", descending=True)


def _empty_score_frame() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "symbol": pl.Utf8,
            "signal": pl.Utf8,
            "price": pl.Float64,
            "confidence": pl.Float64,
            "setup_valid": pl.Boolean,
            "trigger_met": pl.Boolean,
            "strategy_name": pl.Utf8,
        }
    )


def latest_buys(df: pl.DataFrame, scan_date=None) -> pl.DataFrame:
    """
    Return the BUY rows. If ``scan_date`` is given, restrict to that date so the
    daily scan only surfaces symbols that triggered today.
    """
    out = df.filter(pl.col("signal") == "BUY")
    if scan_date is not None:
        out = out.filter(pl.col("date") == scan_date)
    return out
