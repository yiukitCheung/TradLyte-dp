"""
Spike benchmark: vectorized full-universe Golden Cross vs. per-symbol loop.

Goal
----
Prove (a) the vectorized ``.over("symbol")`` implementation produces IDENTICAL
BUY signals to the existing per-symbol ``GoldenCrossStrategy``, and (b) it is
dramatically faster — before we wire any of this into the pipeline.

Run
---
    cd cloud/shared
    python -m analytics_core._spikes.bench_vectorized_golden_cross --symbols 500 --days 800

Synthetic data is a per-symbol geometric random walk, so SMA-50/200 crosses
occur naturally. No RDS / S3 / network required.
"""

from __future__ import annotations

import argparse
import time
from datetime import date, timedelta

import polars as pl

from analytics_core.vectorized_scanner import golden_cross_signals, resample_long
from analytics_core.strategies.library import GoldenCrossStrategy


def make_universe(n_symbols: int, n_days: int, seed: int = 7) -> pl.DataFrame:
    """Build a long-format [date, symbol, OHLCV] frame of random-walk prices."""
    import random

    rng = random.Random(seed)
    start = date(2020, 1, 1)
    dates = [start + timedelta(days=i) for i in range(n_days)]

    symbols, all_dates, opens, highs, lows, closes, volumes = [], [], [], [], [], [], []
    for s in range(n_symbols):
        sym = f"SYM{s:04d}"
        price = rng.uniform(20, 200)
        for d in dates:
            drift = rng.uniform(-0.03, 0.031)  # slight upward bias → crosses happen
            new_price = max(1.0, price * (1 + drift))
            o = price
            c = new_price
            hi = max(o, c) * (1 + rng.uniform(0, 0.01))
            lo = min(o, c) * (1 - rng.uniform(0, 0.01))
            symbols.append(sym)
            all_dates.append(d)
            opens.append(o)
            highs.append(hi)
            lows.append(lo)
            closes.append(c)
            volumes.append(float(rng.randint(10_000, 1_000_000)))
            price = new_price

    return pl.DataFrame(
        {
            "date": all_dates,
            "symbol": symbols,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    ).with_columns(pl.col("date").cast(pl.Date))


def run_per_symbol_loop(df: pl.DataFrame) -> set:
    """Current approach: run the library strategy once per symbol."""
    strat = GoldenCrossStrategy()
    buys = set()
    for sym in df["symbol"].unique().to_list():
        sub = df.filter(pl.col("symbol") == sym).sort("date")
        out = strat.run(sub)
        hits = out.filter(pl.col("signal") == "BUY")
        for d in hits["date"].to_list():
            buys.add((sym, d))
    return buys


def run_vectorized(df: pl.DataFrame) -> set:
    """New approach: one vectorized pass over the whole universe."""
    out = golden_cross_signals(df)
    hits = out.filter(pl.col("signal") == "BUY")
    return set(zip(hits["symbol"].to_list(), hits["date"].to_list()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=int, default=500)
    ap.add_argument("--days", type=int, default=800)
    args = ap.parse_args()

    print(f"Generating universe: {args.symbols} symbols x {args.days} days "
          f"= {args.symbols * args.days:,} rows ...")
    df = make_universe(args.symbols, args.days)
    print(f"Frame: {df.height:,} rows, {df['symbol'].n_unique()} symbols\n")

    t0 = time.perf_counter()
    loop_buys = run_per_symbol_loop(df)
    t_loop = time.perf_counter() - t0

    t0 = time.perf_counter()
    vec_buys = run_vectorized(df)
    t_vec = time.perf_counter() - t0

    print("== Correctness ==")
    print(f"  per-symbol BUY signals: {len(loop_buys):,}")
    print(f"  vectorized BUY signals: {len(vec_buys):,}")
    only_loop = loop_buys - vec_buys
    only_vec = vec_buys - loop_buys
    match = not only_loop and not only_vec
    print(f"  identical: {match}")
    if not match:
        print(f"  only in loop (sample): {list(only_loop)[:5]}")
        print(f"  only in vec  (sample): {list(only_vec)[:5]}")

    print("\n== Performance ==")
    print(f"  per-symbol loop : {t_loop:8.3f} s")
    print(f"  vectorized      : {t_vec:8.3f} s")
    if t_vec > 0:
        print(f"  speedup         : {t_loop / t_vec:8.1f}x")

    print("\n== 3d resample sanity ==")
    t0 = time.perf_counter()
    df3 = resample_long(df, 3)
    t_rs = time.perf_counter() - t0
    print(f"  1d rows={df.height:,} -> 3d rows={df3.height:,}  ({t_rs:.3f} s)")
    v3 = run_vectorized(df3)
    print(f"  3d vectorized BUY signals: {len(v3):,}")


if __name__ == "__main__":
    main()
