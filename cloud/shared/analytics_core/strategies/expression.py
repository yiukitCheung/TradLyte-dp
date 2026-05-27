"""
Expression-Tree Evaluator
=========================

Walks a :class:`~analytics_core.models.ConditionNode` tree and returns a
single Polars boolean expression. Indicator and pattern columns are
materialised on-demand via the central registries in
``indicators/technicals.py`` and a local pattern registry — so adding a new
indicator or pattern requires no edits to the evaluator.

Public API
----------

* :func:`eval_operand(node, df)` → ``(df, pl.Expr)``
    Returns the DataFrame (possibly with a new indicator column added) and a
    Polars expression referencing that column / OHLCV field / literal.

* :func:`eval_condition(node, df)` → ``(df, pl.Expr)``
    Returns the DataFrame (with all referenced indicator/pattern columns
    materialised) and a boolean Polars expression that can be assigned with
    ``df.with_columns(expr.alias('setup_valid'))``.

Both functions accept either a Pydantic model instance (``CompareNode``,
``IndicatorOperand``, …) or a raw ``dict`` matching the same schema, which
makes them convenient to call from JSON without going through Pydantic.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Tuple, Union

import polars as pl
from pydantic import BaseModel

from ..indicators.patterns import (
    detect_doji,
    detect_engulfing_bearish,
    detect_engulfing_bullish,
    detect_evening_star,
    detect_green_candle,
    detect_hammer,
    detect_morning_star,
    detect_red_candle,
    detect_shooting_star,
)
from ..indicators.technicals import resolve_indicator


# ----------------------------------------------------------------------------
# Pattern registry
# ----------------------------------------------------------------------------
#
# Each entry maps a canonical pattern name to:
#   * ``detect``: function ``(df) -> df`` that adds the boolean output column.
#   * ``column``: the name of the boolean output column produced by ``detect``.

PATTERN_REGISTRY: Dict[str, Dict[str, Any]] = {
    "DOJI":              {"detect": detect_doji,              "column": "doji"},
    "HAMMER":            {"detect": detect_hammer,            "column": "hammer"},
    "SHOOTING_STAR":     {"detect": detect_shooting_star,     "column": "shooting_star"},
    "MORNING_STAR":      {"detect": detect_morning_star,      "column": "morning_star"},
    "EVENING_STAR":      {"detect": detect_evening_star,      "column": "evening_star"},
    "GREEN_CANDLE":      {"detect": detect_green_candle,      "column": "green_candle"},
    "RED_CANDLE":        {"detect": detect_red_candle,        "column": "red_candle"},
    "ENGULFING_BULLISH": {"detect": detect_engulfing_bullish, "column": "engulfing_bullish"},
    "ENGULFING_BEARISH": {"detect": detect_engulfing_bearish, "column": "engulfing_bearish"},
    # Common alias spellings used in older payloads.
    "BULLISH_ENGULFING": {"detect": detect_engulfing_bullish, "column": "engulfing_bullish"},
    "BEARISH_ENGULFING": {"detect": detect_engulfing_bearish, "column": "engulfing_bearish"},
}


# Compact alias type. Anywhere we accept "a Pydantic model or raw dict" we use
# ``Mapping[str, Any]`` to keep the surface consistent.
NodeLike = Union[BaseModel, Mapping[str, Any]]


def _as_dict(node: NodeLike) -> Dict[str, Any]:
    """Normalise a Pydantic model or raw mapping into a plain dict."""
    if isinstance(node, BaseModel):
        # ``model_dump`` is the Pydantic v2 way; falls back gracefully for v1.
        try:
            return node.model_dump(exclude_none=True)
        except AttributeError:
            return node.dict(exclude_none=True)  # type: ignore[attr-defined]
    if isinstance(node, Mapping):
        return dict(node)
    raise TypeError(f"Cannot interpret {type(node).__name__!r} as a DSL node")


# ----------------------------------------------------------------------------
# Operand evaluator
# ----------------------------------------------------------------------------

def eval_operand(node: NodeLike, df: pl.DataFrame) -> Tuple[pl.DataFrame, pl.Expr]:
    """
    Resolve a leaf operand to a Polars expression.

    The expression references either:
      * a parametric indicator column (materialised on demand), or
      * a raw OHLCV column, or
      * a numeric literal.

    Returns ``(df, expr)``. ``df`` may be a new DataFrame with the indicator
    column added; ``expr`` is the Polars expression to use in comparisons.
    """
    spec = _as_dict(node)

    if "indicator" in spec:
        resolved = resolve_indicator(
            indicator=spec["indicator"],
            params=spec.get("params"),
            output=spec.get("output"),
        )
        df = resolved["calc"](df)
        return df, pl.col(resolved["column"])

    if "price" in spec:
        column = spec["price"]
        if column not in ("open", "high", "low", "close", "volume"):
            raise ValueError(f"Unknown price column: {column!r}")
        return df, pl.col(column)

    if "const" in spec:
        return df, pl.lit(spec["const"])

    raise ValueError(
        "Operand must contain one of 'indicator', 'price', or 'const'. "
        f"Got keys: {sorted(spec)}"
    )


# ----------------------------------------------------------------------------
# Condition evaluator
# ----------------------------------------------------------------------------

# Comparison op → callable producing a Polars expression.
_COMPARE_OPS: Dict[str, Callable[[pl.Expr, pl.Expr], pl.Expr]] = {
    "GT":  lambda a, b: a > b,
    "LT":  lambda a, b: a < b,
    "GTE": lambda a, b: a >= b,
    "LTE": lambda a, b: a <= b,
    "EQ":  lambda a, b: a == b,
    "NEQ": lambda a, b: a != b,
    # Crossovers compare current vs prior bar to detect the moment a series
    # crosses the other. NULL on the very first bar is desired (cannot be a
    # cross with no prior value).
    "CROSS_ABOVE": lambda a, b: (a > b) & (a.shift(1) <= b.shift(1)),
    "CROSS_BELOW": lambda a, b: (a < b) & (a.shift(1) >= b.shift(1)),
}


def _eval_pattern(spec: Dict[str, Any], df: pl.DataFrame) -> Tuple[pl.DataFrame, pl.Expr]:
    pattern = spec.get("pattern")
    if not pattern:
        raise ValueError("PATTERN node requires a 'pattern' field")
    entry = PATTERN_REGISTRY.get(pattern.upper())
    if entry is None:
        raise ValueError(
            f"Unknown pattern {pattern!r}. Known: {sorted(PATTERN_REGISTRY)}"
        )
    if entry["column"] not in df.columns:
        df = entry["detect"](df)
    return df, pl.col(entry["column"])


def eval_condition(node: NodeLike, df: pl.DataFrame) -> Tuple[pl.DataFrame, pl.Expr]:
    """
    Resolve a condition tree to a boolean Polars expression.

    Walks any combination of comparators, candle patterns, AND/OR/NOT
    combinators, and operand leaves. Materialises any indicator or pattern
    columns required along the way. Returns ``(df, expr)``.
    """
    spec = _as_dict(node)
    op = (spec.get("op") or "").upper()
    if not op:
        raise ValueError(f"Condition node missing 'op' field: {spec!r}")

    if op in _COMPARE_OPS:
        if "left" not in spec or "right" not in spec:
            raise ValueError(f"{op} requires 'left' and 'right' operands: {spec!r}")
        df, left_expr = eval_operand(spec["left"], df)
        df, right_expr = eval_operand(spec["right"], df)
        return df, _COMPARE_OPS[op](left_expr, right_expr)

    if op == "PATTERN":
        return _eval_pattern(spec, df)

    if op == "AND":
        children = spec.get("conditions") or []
        if not children:
            raise ValueError("AND requires at least one child condition")
        exprs = []
        for child in children:
            df, expr = eval_condition(child, df)
            exprs.append(expr)
        return df, pl.all_horizontal(exprs)

    if op == "OR":
        children = spec.get("conditions") or []
        if not children:
            raise ValueError("OR requires at least one child condition")
        exprs = []
        for child in children:
            df, expr = eval_condition(child, df)
            exprs.append(expr)
        return df, pl.any_horizontal(exprs)

    if op == "NOT":
        child = spec.get("condition")
        if child is None:
            raise ValueError("NOT requires a 'condition' child")
        df, expr = eval_condition(child, df)
        return df, ~expr

    raise ValueError(f"Unknown condition op: {op!r}")


__all__ = [
    "PATTERN_REGISTRY",
    "eval_operand",
    "eval_condition",
]
