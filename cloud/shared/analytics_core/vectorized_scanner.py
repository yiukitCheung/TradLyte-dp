"""
Vectorized Full-Universe Scanner

Runs a strategy across the ENTIRE symbol universe in a single Polars pass,
instead of looping one symbol at a time. The input is the consolidated
long-format snapshot produced by the snapshot_builder Lambda:

    [date: Date, symbol: Utf8, open, high, low, close, volume: Float64]

Why this is fast
----------------
Polars window functions (``.over("symbol")``) compute per-symbol rolling
indicators and shifts across all symbols at once, using a single vectorized
engine pass with no Python-level loop. A 10k-symbol x 5y frame (~12M rows)
fits comfortably in RAM and scans in well under a second per strategy.

CRITICAL correctness rule
--------------------------
Every operation that crosses row boundaries — ``rolling_mean``,
``rolling_std``, ``shift``, ``diff``, ``ewm_mean`` — MUST be wrapped in
``.over("symbol")``. Without it, one symbol's tail bleeds into the next
symbol's head (e.g. AAPL's last 49 closes contaminate AAL's first SMA-50),
silently corrupting signals. The per-symbol library strategies omit ``.over``
because they assume a single-symbol frame; this module re-implements them
universe-aware.

Timeframes
----------
The snapshot is 1d. Longer timeframes (e.g. 3d "long-term") are derived on the
fly via :func:`resample_long` — N consecutive trading rows aggregated per
symbol — and never persisted.
"""

from __future__ import annotations

import polars as pl

SNAPSHOT_COLUMNS = ["date", "symbol", "open", "high", "low", "close", "volume"]


# ---------------------------------------------------------------------------
# Timeframe resampling (1d base → N trading-day bars)
# ---------------------------------------------------------------------------

def resample_long(df: pl.DataFrame, n_days: int) -> pl.DataFrame:
    """
    Resample a long-format 1d frame to N-trading-day bars, per symbol.

    Uses row grouping (every ``n_days`` rows within each symbol) rather than a
    calendar window, so weekends/holidays don't create empty or short bars.
    Fully vectorized — no per-symbol Python loop.
    """
    if n_days <= 1:
        return df.sort(["symbol", "date"])

    grouped = (
        df.sort(["symbol", "date"])
        .with_columns(
            (pl.int_range(pl.len()).over("symbol") // n_days).alias("_bucket")
        )
        .group_by(["symbol", "_bucket"])
        .agg(
            pl.col("date").last().alias("date"),
            pl.col("open").first().alias("open"),
            pl.col("high").max().alias("high"),
            pl.col("low").min().alias("low"),
            pl.col("close").last().alias("close"),
            pl.col("volume").sum().alias("volume"),
        )
        .drop("_bucket")
        .sort(["symbol", "date"])
    )
    return grouped.select(SNAPSHOT_COLUMNS)


# ---------------------------------------------------------------------------
# Golden Cross — vectorized over the full universe
# ---------------------------------------------------------------------------

def golden_cross_signals(
    df: pl.DataFrame,
    fast_period: int = 50,
    slow_period: int = 200,
) -> pl.DataFrame:
    """
    Vectorized Golden Cross across all symbols.

    Setup:   SMA(fast) > SMA(slow)                       (uptrend)
    Trigger: SMA(fast) crosses above SMA(slow)           (golden cross)
    Exit:    death cross OR 5% stop loss anchor

    Adds columns: sma_{fast}, sma_{slow}, setup_valid, signal,
    exit_signal, stop_loss_price. Mirrors
    ``strategies.library.GoldenCrossStrategy`` exactly, but every rolling /
    shift is partitioned ``.over("symbol")``.
    """
    fast_col = f"sma_{fast_period}"
    slow_col = f"sma_{slow_period}"

    df = df.sort(["symbol", "date"]).with_columns(
        [
            pl.col("close").rolling_mean(window_size=fast_period).over("symbol").alias(fast_col),
            pl.col("close").rolling_mean(window_size=slow_period).over("symbol").alias(slow_col),
        ]
    )

    df = df.with_columns(
        [
            (pl.col(fast_col) > pl.col(slow_col)).alias("setup_valid"),
            pl.col(fast_col).shift(1).over("symbol").alias("_fast_prev"),
            pl.col(slow_col).shift(1).over("symbol").alias("_slow_prev"),
        ]
    )

    golden_cross = (pl.col(fast_col) > pl.col(slow_col)) & (pl.col("_fast_prev") <= pl.col("_slow_prev"))
    death_cross = (pl.col(fast_col) < pl.col(slow_col)) & (pl.col("_fast_prev") >= pl.col("_slow_prev"))

    df = df.with_columns(
        [
            pl.when(pl.col("setup_valid") & golden_cross)
            .then(pl.lit("BUY"))
            .otherwise(pl.lit("HOLD"))
            .alias("signal"),
            pl.when(death_cross).then(pl.lit("SELL")).otherwise(None).alias("exit_signal"),
            (pl.col("close") * 0.95).alias("stop_loss_price"),
        ]
    )

    return df.drop(["_fast_prev", "_slow_prev"])


def latest_buys(df: pl.DataFrame, scan_date=None) -> pl.DataFrame:
    """
    Return the BUY rows. If ``scan_date`` is given, restrict to that date so the
    daily scan only surfaces symbols that triggered today.
    """
    out = df.filter(pl.col("signal") == "BUY")
    if scan_date is not None:
        out = out.filter(pl.col("date") == scan_date)
    return out
