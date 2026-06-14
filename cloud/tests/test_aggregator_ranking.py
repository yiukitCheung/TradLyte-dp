"""Unit tests for scanner signal ranking."""

from analytics_core.models import SignalResult
from processing.batch_jobs.aggregator import rank_signals


def _signal(symbol: str, confidence: float, strategy: str = "golden_cross") -> SignalResult:
    return SignalResult(
        symbol=symbol,
        date="2026-06-05",
        signal="BUY",
        price=100.0,
        confidence=confidence,
        setup_valid=True,
        trigger_met=True,
        metadata={"strategy_name": strategy},
    )


def test_rank_signals_dense_rank_and_unique_symbol():
    signals = [
        _signal("AAA", 0.9),
        _signal("BBB", 0.9),
        _signal("CCC", 0.5),
        _signal("DDD", 0.5),
        _signal("EEE", 0.1),
    ]
    ranked = rank_signals(signals, top_k=2, unique_symbol=True)
    symbols = [s.symbol for s in ranked]
    assert symbols == ["AAA", "BBB", "CCC", "DDD"]
    assert all(s.metadata["dense_rank"] <= 2 for s in ranked)


def test_rank_signals_groups_by_strategy_when_by_pick_type():
    signals = [
        _signal("AAA", 0.9, "golden_cross"),
        _signal("BBB", 0.8, "vegas_channel_short_term"),
    ]
    grouped = rank_signals(signals, top_k=1, by_pick_type=True)
    assert set(grouped.keys()) == {"golden_cross", "vegas_channel_short_term"}
    assert grouped["golden_cross"][0].symbol == "AAA"


def test_rank_signals_breaks_confidence_ties_by_market_cap():
    signals = [
        _signal("AAA", 1.0, "vegas_channel_short_term"),
        _signal("MSFT", 1.0, "vegas_channel_short_term"),
        _signal("NVDA", 1.0, "vegas_channel_short_term"),
    ]
    market_caps = {
        "AAA": 1_000_000_000,
        "MSFT": 3_000_000_000_000,
        "NVDA": 2_000_000_000_000,
    }

    ranked = rank_signals(
        signals,
        top_k=1,
        unique_symbol=True,
        market_caps=market_caps,
    )

    symbols = [s.symbol for s in ranked]
    assert symbols == ["MSFT", "NVDA", "AAA"]
    assert ranked[0].metadata["market_cap"] == 3_000_000_000_000


def test_rank_signals_puts_missing_market_cap_last_on_tie():
    signals = [
        _signal("AAA", 0.5),
        _signal("MSFT", 0.5),
    ]
    ranked = rank_signals(
        signals,
        top_k=1,
        market_caps={"MSFT": 500_000_000_000},
    )

    assert [s.symbol for s in ranked] == ["MSFT", "AAA"]
    assert "market_cap" not in ranked[1].metadata
