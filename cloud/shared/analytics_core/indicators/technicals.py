"""
Technical Indicators using Polars.

Each indicator emits a **parametric** column name derived from its arguments.
That contract lets a single DataFrame carry several instances of the same
indicator with different parameters side-by-side (e.g. ``rsi_13`` and
``rsi_16``), which is required by the JSON expression DSL in
``strategies/builder.py``.

Conventions
-----------
- Single-output indicators use ``<name>_<param>``:
    * ``rsi_{period}``      e.g. ``rsi_14``
    * ``sma_{period}``      e.g. ``sma_50``
    * ``ema_{period}``      e.g. ``ema_200``
    * ``atr_{period}``      e.g. ``atr_14``
- Multi-output indicators use ``<name>_<role>_<params...>``:
    * ``macd_{fast}_{slow}``                 e.g. ``macd_12_26``
    * ``macd_signal_{fast}_{slow}_{signal}`` e.g. ``macd_signal_12_26_9``
    * ``macd_hist_{fast}_{slow}_{signal}``   e.g. ``macd_hist_12_26_9``
    * ``bb_{role}_{period}_{std_dev}``       e.g. ``bb_upper_20_2.0``
    * ``stoch_{role}_{k}_{d}``               e.g. ``stoch_k_14_3``

Callers should resolve column names via ``column_name(indicator, **params)``
or the per-indicator helpers (e.g. ``rsi_col(period)``) rather than
hard-coding strings, so a single refactor here propagates everywhere.
"""

from typing import Any, Dict, Optional

import polars as pl


# ----------------------------------------------------------------------------
# Column-name helpers
# ----------------------------------------------------------------------------

def _fmt_std(std_dev: float) -> str:
    # Keep two-band keys readable: 2 -> "2", 2.5 -> "2.5".
    s = f"{std_dev:g}"
    return s


def rsi_col(period: int) -> str:
    return f"rsi_{int(period)}"


def sma_col(period: int) -> str:
    return f"sma_{int(period)}"


def ema_col(period: int) -> str:
    return f"ema_{int(period)}"


def atr_col(period: int) -> str:
    return f"atr_{int(period)}"


def macd_cols(fast_period: int, slow_period: int, signal_period: int) -> Dict[str, str]:
    """Return the three MACD output column names keyed by role."""
    return {
        "macd": f"macd_{int(fast_period)}_{int(slow_period)}",
        "signal": f"macd_signal_{int(fast_period)}_{int(slow_period)}_{int(signal_period)}",
        "histogram": f"macd_hist_{int(fast_period)}_{int(slow_period)}_{int(signal_period)}",
    }


def bb_cols(period: int, std_dev: float) -> Dict[str, str]:
    """Return the three Bollinger-Bands output column names keyed by role."""
    suffix = f"{int(period)}_{_fmt_std(std_dev)}"
    return {
        "middle": f"bb_middle_{suffix}",
        "upper": f"bb_upper_{suffix}",
        "lower": f"bb_lower_{suffix}",
    }


def stoch_cols(k_period: int, d_period: int) -> Dict[str, str]:
    """Return the two Stochastic output column names keyed by role."""
    return {
        "k": f"stoch_k_{int(k_period)}_{int(d_period)}",
        "d": f"stoch_d_{int(k_period)}_{int(d_period)}",
    }


# ----------------------------------------------------------------------------
# Indicators
# ----------------------------------------------------------------------------

def calculate_rsi(df: pl.DataFrame, period: int = 14, price_col: str = "close") -> pl.DataFrame:
    """Calculate RSI. Emits column ``rsi_{period}``."""
    out_col = rsi_col(period)
    if out_col in df.columns:
        return df

    delta = pl.col(price_col).diff()
    gain = pl.when(delta > 0).then(delta).otherwise(0).fill_null(0)
    loss = pl.when(delta < 0).then(-delta).otherwise(0).fill_null(0)

    avg_gain = gain.rolling_mean(window_size=period)
    avg_loss = loss.rolling_mean(window_size=period)

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return df.with_columns(rsi.alias(out_col))


def calculate_sma(df: pl.DataFrame, period: int, price_col: str = "close") -> pl.DataFrame:
    """Calculate SMA. Emits column ``sma_{period}``."""
    out_col = sma_col(period)
    if out_col in df.columns:
        return df
    sma = pl.col(price_col).rolling_mean(window_size=period)
    return df.with_columns(sma.alias(out_col))


def calculate_ema(df: pl.DataFrame, period: int, price_col: str = "close") -> pl.DataFrame:
    """Calculate EMA. Emits column ``ema_{period}``."""
    out_col = ema_col(period)
    if out_col in df.columns:
        return df
    alpha = 2.0 / (period + 1)
    ema = pl.col(price_col).ewm_mean(alpha=alpha, adjust=False)
    return df.with_columns(ema.alias(out_col))


def calculate_macd(
    df: pl.DataFrame,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
    price_col: str = "close",
) -> pl.DataFrame:
    """
    Calculate MACD. Emits three parametric columns; see ``macd_cols``.
    """
    cols = macd_cols(fast_period, slow_period, signal_period)
    if all(c in df.columns for c in cols.values()):
        return df

    fast_ema = pl.col(price_col).ewm_mean(alpha=2.0 / (fast_period + 1), adjust=False)
    slow_ema = pl.col(price_col).ewm_mean(alpha=2.0 / (slow_period + 1), adjust=False)
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm_mean(alpha=2.0 / (signal_period + 1), adjust=False)
    histogram = macd_line - signal_line

    return df.with_columns([
        macd_line.alias(cols["macd"]),
        signal_line.alias(cols["signal"]),
        histogram.alias(cols["histogram"]),
    ])


def calculate_bollinger_bands(
    df: pl.DataFrame,
    period: int = 20,
    std_dev: float = 2.0,
    price_col: str = "close",
) -> pl.DataFrame:
    """Calculate Bollinger Bands. See ``bb_cols`` for output column names."""
    cols = bb_cols(period, std_dev)
    if all(c in df.columns for c in cols.values()):
        return df

    sma = pl.col(price_col).rolling_mean(window_size=period)
    std = pl.col(price_col).rolling_std(window_size=period)
    upper = sma + (std * std_dev)
    lower = sma - (std * std_dev)

    return df.with_columns([
        sma.alias(cols["middle"]),
        upper.alias(cols["upper"]),
        lower.alias(cols["lower"]),
    ])


def calculate_atr(df: pl.DataFrame, period: int = 14) -> pl.DataFrame:
    """Calculate ATR. Emits column ``atr_{period}``."""
    out_col = atr_col(period)
    if out_col in df.columns:
        return df

    high_low = pl.col("high") - pl.col("low")
    high_close = (pl.col("high") - pl.col("close").shift(1)).abs()
    low_close = (pl.col("low") - pl.col("close").shift(1)).abs()
    tr = pl.max_horizontal([high_low, high_close, low_close])
    atr = tr.rolling_mean(window_size=period)
    return df.with_columns(atr.alias(out_col))


def calculate_stochastic(
    df: pl.DataFrame,
    k_period: int = 14,
    d_period: int = 3,
) -> pl.DataFrame:
    """Calculate Stochastic Oscillator. See ``stoch_cols`` for output names."""
    cols = stoch_cols(k_period, d_period)
    if all(c in df.columns for c in cols.values()):
        return df

    lowest_low = pl.col("low").rolling_min(window_size=k_period)
    highest_high = pl.col("high").rolling_max(window_size=k_period)
    k_percent = 100 * ((pl.col("close") - lowest_low) / (highest_high - lowest_low))
    d_percent = k_percent.rolling_mean(window_size=d_period)
    return df.with_columns([
        k_percent.alias(cols["k"]),
        d_percent.alias(cols["d"]),
    ])


# ----------------------------------------------------------------------------
# Indicator registry — single source of truth for DSL → column resolution
# ----------------------------------------------------------------------------
#
# Each entry maps an indicator name to:
#   * ``calc``: function that adds the parametric column(s) to a DataFrame.
#   * ``defaults``: default parameter dict (merged with user params).
#   * ``columns``: callable params -> dict[role, column_name]. The DSL uses
#     the ``output`` role hint (default = first key) to pick which column the
#     operand resolves to.
#
# Adding a new indicator is a single registry entry plus its ``calculate_*``
# function — no per-strategy code changes are required to expose it to users.

INDICATOR_REGISTRY: Dict[str, Dict[str, Any]] = {
    "RSI": {
        "calc": calculate_rsi,
        "defaults": {"period": 14},
        "columns": lambda p: {"default": rsi_col(p["period"])},
    },
    "SMA": {
        "calc": calculate_sma,
        "defaults": {"period": 20},
        "columns": lambda p: {"default": sma_col(p["period"])},
    },
    "EMA": {
        "calc": calculate_ema,
        "defaults": {"period": 20},
        "columns": lambda p: {"default": ema_col(p["period"])},
    },
    "MACD": {
        "calc": calculate_macd,
        "defaults": {"fast_period": 12, "slow_period": 26, "signal_period": 9},
        "columns": lambda p: {
            "default": macd_cols(p["fast_period"], p["slow_period"], p["signal_period"])["macd"],
            "macd": macd_cols(p["fast_period"], p["slow_period"], p["signal_period"])["macd"],
            "signal": macd_cols(p["fast_period"], p["slow_period"], p["signal_period"])["signal"],
            "histogram": macd_cols(p["fast_period"], p["slow_period"], p["signal_period"])["histogram"],
        },
    },
    "BB": {
        "calc": calculate_bollinger_bands,
        "defaults": {"period": 20, "std_dev": 2.0},
        "columns": lambda p: {
            "default": bb_cols(p["period"], p["std_dev"])["middle"],
            "middle": bb_cols(p["period"], p["std_dev"])["middle"],
            "upper": bb_cols(p["period"], p["std_dev"])["upper"],
            "lower": bb_cols(p["period"], p["std_dev"])["lower"],
        },
    },
    "ATR": {
        "calc": calculate_atr,
        "defaults": {"period": 14},
        "columns": lambda p: {"default": atr_col(p["period"])},
    },
    "STOCH": {
        "calc": calculate_stochastic,
        "defaults": {"k_period": 14, "d_period": 3},
        "columns": lambda p: {
            "default": stoch_cols(p["k_period"], p["d_period"])["k"],
            "k": stoch_cols(p["k_period"], p["d_period"])["k"],
            "d": stoch_cols(p["k_period"], p["d_period"])["d"],
        },
    },
}


def resolve_indicator(
    indicator: str,
    params: Optional[Dict[str, Any]] = None,
    output: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Resolve an indicator name + params to a {column, calc, params} triple.

    Returns a dict with:
        * ``column``: the resolved Polars column name to read from.
        * ``calc``:   function ``(df) -> df`` that ensures the column exists.
        * ``params``: merged params (user overrides over registry defaults).

    Raises ``ValueError`` if the indicator name or output role is unknown.
    """
    key = indicator.upper()
    entry = INDICATOR_REGISTRY.get(key)
    if entry is None:
        raise ValueError(
            f"Unknown indicator {indicator!r}. "
            f"Known: {sorted(INDICATOR_REGISTRY)}"
        )

    merged: Dict[str, Any] = {**entry["defaults"], **(params or {})}
    column_map = entry["columns"](merged)
    role = (output or "default").lower()
    if role not in column_map:
        raise ValueError(
            f"Indicator {indicator!r} has no output role {role!r}. "
            f"Available roles: {sorted(column_map)}"
        )

    def _calc(df: pl.DataFrame, _fn=entry["calc"], _params=merged) -> pl.DataFrame:
        return _fn(df, **_params)

    return {"column": column_map[role], "calc": _calc, "params": merged}


def calculate_all_indicators(df: pl.DataFrame) -> pl.DataFrame:
    """
    Pre-compute a curated bundle of canonical indicators for scanner /
    notebook use. All columns use the new parametric naming convention.

    Args:
        df: DataFrame with OHLCV data.

    Returns:
        DataFrame with the canonical indicator columns added.
    """
    ohlcv = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    if ohlcv:
        df = df.with_columns([pl.col(c).cast(pl.Float64) for c in ohlcv])

    # RSI (default period)
    df = calculate_rsi(df, period=14)

    # SMAs (canonical periods used across the codebase)
    for period in (20, 50, 200):
        df = calculate_sma(df, period=period)

    # EMAs (Vegas channel + Fibonacci ladder)
    for period in (8, 13, 21, 55, 89, 144, 169):
        df = calculate_ema(df, period=period)

    # MACD (default 12/26/9)
    df = calculate_macd(df)

    # Bollinger Bands (default 20 / 2σ)
    df = calculate_bollinger_bands(df)

    # ATR (default 14)
    df = calculate_atr(df)

    # Stochastic (default 14 / 3)
    df = calculate_stochastic(df)

    return df


__all__ = [
    # Indicators
    "calculate_rsi",
    "calculate_sma",
    "calculate_ema",
    "calculate_macd",
    "calculate_bollinger_bands",
    "calculate_atr",
    "calculate_stochastic",
    "calculate_all_indicators",
    # Column-name helpers
    "rsi_col",
    "sma_col",
    "ema_col",
    "atr_col",
    "macd_cols",
    "bb_cols",
    "stoch_cols",
    # Registry API
    "INDICATOR_REGISTRY",
    "resolve_indicator",
]
