"""
Backtester Engine

Simulates strategy execution with position tracking and performance metrics.
Supports stop loss, take profit, trailing stops, and calculates comprehensive metrics.
"""

import polars as pl
from typing import List, Dict, Any, Optional
from datetime import date, datetime
from dataclasses import dataclass
import math
from .strategies.base import BaseStrategy
from .executor import MultiTimeframeExecutor


# Trading days in a calendar year (US equities convention). Used as the
# fallback periods/year when the bar spacing cannot be inferred.
TRADING_DAYS_PER_YEAR = 252


def _infer_periods_per_year(data: pl.DataFrame) -> int:
    """
    Estimate the number of bars per year from the median spacing of the
    ``date`` column. Falls back to 252 (daily trading days) when there are
    fewer than two bars or the median spacing is non-positive.

    Examples
    --------
    - 1d bars  → median ~= 1 day  → 252
    - 3d bars  → median ~= 3 days → 84
    - 5d bars  → median ~= 5 days → 50
    - 1h bars  → median ~= 1 hour → ~1638 (252 * 6.5 trading hours)
    """
    if data.height < 2 or 'date' not in data.columns:
        return TRADING_DAYS_PER_YEAR

    # diff() on a Date/Datetime column returns a Duration. Convert to seconds
    # via total_seconds() so we handle both date and intraday timeframes.
    try:
        deltas = (
            data.sort('date')
            .select(pl.col('date').diff().dt.total_seconds().alias('s'))
            .drop_nulls()
        )
    except Exception:
        return TRADING_DAYS_PER_YEAR

    if deltas.height == 0:
        return TRADING_DAYS_PER_YEAR

    median_seconds = float(deltas['s'].median() or 0.0)
    if median_seconds <= 0:
        return TRADING_DAYS_PER_YEAR

    # Trading year in seconds: 252 trading days * 6.5 trading hours when the
    # spacing is sub-daily, otherwise 252 days.
    seconds_per_trading_day = 6.5 * 3600
    if median_seconds < 86400:  # intraday
        return max(1, int(round(TRADING_DAYS_PER_YEAR * seconds_per_trading_day / median_seconds)))
    median_days = median_seconds / 86400.0
    return max(1, int(round(TRADING_DAYS_PER_YEAR / median_days)))


@dataclass
class Position:
    """Represents a trading position"""
    entry_date: date
    entry_price: float
    # peak_price is the highest close seen since entry; used by the trailing
    # stop. Defaults to entry_price on open (see __post_init__).
    peak_price: float = 0.0
    exit_date: Optional[date] = None
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    holding_days: Optional[int] = None
    exit_reason: Optional[str] = None  # 'stop_loss', 'take_profit', 'signal', 'trailing_stop', 'time_based'

    def __post_init__(self):
        # Initialise peak to entry so trailing-stop math never references 0.0.
        if self.peak_price < self.entry_price:
            self.peak_price = self.entry_price

    def is_open(self) -> bool:
        """Check if position is still open"""
        return self.exit_date is None

    def update_peak(self, price: float) -> None:
        """Track the highest price observed since entry (for trailing stop)."""
        if price > self.peak_price:
            self.peak_price = price

    def close(self, exit_date: date, exit_price: float, reason: str):
        """Close the position"""
        self.exit_date = exit_date
        self.exit_price = exit_price
        self.pnl = exit_price - self.entry_price
        self.pnl_pct = (exit_price / self.entry_price) - 1.0
        self.holding_days = (exit_date - self.entry_date).days
        self.exit_reason = reason


@dataclass
class BacktestResult:
    """Backtest performance results"""
    total_return: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    max_drawdown: float
    max_drawdown_pct: float
    sharpe_ratio: float
    equity_curve: List[float]
    trades: List[Position]
    initial_capital: float
    final_capital: float
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            'total_return': self.total_return,
            'total_return_pct': (self.final_capital / self.initial_capital) - 1.0,
            'total_trades': self.total_trades,
            'winning_trades': self.winning_trades,
            'losing_trades': self.losing_trades,
            'win_rate': self.win_rate,
            'avg_win': self.avg_win,
            'avg_loss': self.avg_loss,
            'profit_factor': self.profit_factor,
            'max_drawdown': self.max_drawdown,
            'max_drawdown_pct': self.max_drawdown_pct,
            'sharpe_ratio': self.sharpe_ratio,
            'equity_curve': self.equity_curve,
            'initial_capital': self.initial_capital,
            'final_capital': self.final_capital,
            'trades': [
                {
                    'entry_date': str(t.entry_date),
                    'entry_price': t.entry_price,
                    'exit_date': str(t.exit_date) if t.exit_date else None,
                    'exit_price': t.exit_price,
                    'pnl': t.pnl,
                    'pnl_pct': t.pnl_pct,
                    'holding_days': t.holding_days,
                    'exit_reason': t.exit_reason
                }
                for t in self.trades
            ]
        }


class Backtester:
    """
    Backtest engine for strategy validation
    
    Tracks positions, calculates performance metrics, and generates equity curves.
    """
    
    def __init__(
        self,
        initial_capital: float = 10000.0,
        commission: float = 0.0,
        slippage: float = 0.0
    ):
        """
        Initialize backtester
        
        Args:
            initial_capital: Starting capital (default: $10,000)
            commission: Commission per trade (default: 0)
            slippage: Slippage as fraction (default: 0)
        """
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage
    
    def run(
        self,
        strategy: BaseStrategy,
        data: pl.DataFrame,
        stop_loss_pct: Optional[float] = None,
        take_profit_pct: Optional[float] = None,
        trailing_stop_pct: Optional[float] = None,
        max_holding_days: Optional[int] = None
    ) -> BacktestResult:
        """
        Run backtest on strategy
        
        Args:
            strategy: Strategy instance to backtest
            data: OHLCV DataFrame with strategy signals
            stop_loss_pct: Stop loss percentage (e.g., 0.05 = 5%)
            take_profit_pct: Take profit percentage (e.g., 0.10 = 10%)
            trailing_stop_pct: Trailing stop percentage (e.g., 0.03 = 3%)
            max_holding_days: Maximum holding period in days
            
        Returns:
            BacktestResult with performance metrics
        """
        # Use strategy's stop/take profit when not passed to run()
        if stop_loss_pct is None:
            stop_loss_pct = getattr(strategy, 'stop_loss_pct', None)
        if take_profit_pct is None:
            take_profit_pct = getattr(strategy, 'take_profit_pct', None)

        # Execute strategy to get signals
        if 'signal' not in data.columns:
            data = strategy.run(data)

        # Sort by date once; the rest of the loop assumes ascending order.
        data = data.sort('date')

        # Derive the annualisation factor for Sharpe from the actual bar
        # spacing in `data`. For daily bars this gives 252; for 3d Fibonacci
        # bars it gives ~84; for hourly it gives ~6.5*252; etc.
        periods_per_year = _infer_periods_per_year(data)

        # ---- Bookkeeping ---------------------------------------------------
        # We track CASH and SHARES separately so we can mark to market on
        # every bar (equity = cash + shares * close), giving a daily equity
        # curve regardless of trade frequency.
        positions: List[Position] = []
        open_position: Optional[Position] = None
        equity_curve: List[float] = []        # exactly one point per bar
        cash: float = self.initial_capital
        shares: float = 0.0

        for row in data.iter_rows(named=True):
            current_date = row['date']
            current_price = row['close']
            signal = row.get('signal', 'HOLD')

            # 1) Exit logic first so today's stop/take/trail can fire before a
            #    new entry on the same bar.
            if open_position is not None:
                # Update the running peak BEFORE evaluating the trailing stop
                # so it considers today's close.
                open_position.update_peak(current_price)
                should_exit, exit_reason = self._check_exit_conditions(
                    open_position,
                    current_date,
                    current_price,
                    row,
                    stop_loss_pct,
                    take_profit_pct,
                    trailing_stop_pct,
                    max_holding_days,
                )

                if should_exit:
                    exit_price = current_price * (1 - self.slippage) if self.slippage > 0 else current_price
                    open_position.close(current_date, exit_price, exit_reason)
                    # Liquidate the position into cash (minus exit commission).
                    cash += shares * exit_price - self.commission
                    shares = 0.0
                    positions.append(open_position)
                    open_position = None

            # 2) Entry logic (only when flat).
            if signal == 'BUY' and open_position is None:
                entry_price = current_price * (1 + self.slippage) if self.slippage > 0 else current_price
                # Go all-in with whatever cash is left after the entry
                # commission. Fractional shares keep the math clean for an
                # MVP backtester; switch to floor-shares if you ever need
                # discrete-lot accuracy.
                investable = max(cash - self.commission, 0.0)
                if entry_price > 0 and investable > 0:
                    shares = investable / entry_price
                    cash -= shares * entry_price + self.commission
                    open_position = Position(
                        entry_date=current_date,
                        entry_price=entry_price,
                    )

            # 3) Mark to market — exactly one append per bar.
            equity_curve.append(cash + shares * current_price)

        # ---- End-of-data: force-close any still-open position --------------
        if open_position is not None and data.height > 0:
            last_row = data.tail(1).iter_rows(named=True).__next__()
            exit_price = last_row['close'] * (1 - self.slippage) if self.slippage > 0 else last_row['close']
            open_position.close(last_row['date'], exit_price, 'end_of_data')
            cash += shares * exit_price - self.commission
            shares = 0.0
            positions.append(open_position)
            # Overwrite the last MTM point with the realised cash so the
            # curve ends on the actual liquidation value.
            if equity_curve:
                equity_curve[-1] = cash
            else:
                equity_curve.append(cash)

        # After the end-of-data block, shares is always 0; everything is in cash.
        final_capital = cash
        return self._calculate_metrics(
            positions,
            equity_curve,
            self.initial_capital,
            final_capital,
            periods_per_year=periods_per_year,
        )
    
    def _check_exit_conditions(
        self,
        position: Position,
        current_date: date,
        current_price: float,
        row: Dict[str, Any],
        stop_loss_pct: Optional[float],
        take_profit_pct: Optional[float],
        trailing_stop_pct: Optional[float],
        max_holding_days: Optional[int]
    ) -> tuple[bool, str]:
        """Check if position should be exited"""
        
        # Check stop loss
        if stop_loss_pct:
            stop_loss_price = position.entry_price * (1 - stop_loss_pct)
            if current_price <= stop_loss_price:
                return True, 'stop_loss'
        
        # Check take profit
        if take_profit_pct:
            take_profit_price = position.entry_price * (1 + take_profit_pct)
            if current_price >= take_profit_price:
                return True, 'take_profit'
        
        # Trailing stop relative to the highest close observed since entry.
        # position.peak_price is updated each bar by Backtester.run().
        if trailing_stop_pct:
            trailing_stop_price = position.peak_price * (1 - trailing_stop_pct)
            if current_price <= trailing_stop_price:
                return True, 'trailing_stop'
        
        # Check time-based exit
        if max_holding_days:
            holding_days = (current_date - position.entry_date).days
            if holding_days >= max_holding_days:
                return True, 'time_based'
        
        # Check exit signal from strategy
        if row.get('exit_signal') == 'SELL':
            return True, 'signal'
        
        return False, ''
    
    def _calculate_metrics(
        self,
        positions: List[Position],
        equity_curve: List[float],
        initial_capital: float,
        final_capital: float,
        periods_per_year: int = TRADING_DAYS_PER_YEAR,
    ) -> BacktestResult:
        """Calculate performance metrics.

        Args:
            positions: closed positions.
            equity_curve: one mark-to-market value per bar.
            initial_capital: starting cash.
            final_capital: ending cash (after force-closing any open position).
            periods_per_year: annualisation factor for the Sharpe ratio,
                derived from the bar spacing in ``data`` (see
                ``_infer_periods_per_year``). Daily bars → 252.
        """

        if not positions:
            # No trades
            return BacktestResult(
                total_return=0.0,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate=0.0,
                avg_win=0.0,
                avg_loss=0.0,
                profit_factor=0.0,
                max_drawdown=0.0,
                max_drawdown_pct=0.0,
                sharpe_ratio=0.0,
                equity_curve=equity_curve,
                trades=positions,
                initial_capital=initial_capital,
                final_capital=final_capital
            )
        
        # Separate winning and losing trades
        winning_trades = [p for p in positions if p.pnl and p.pnl > 0]
        losing_trades = [p for p in positions if p.pnl and p.pnl <= 0]
        
        total_trades = len(positions)
        winning_count = len(winning_trades)
        losing_count = len(losing_trades)
        win_rate = winning_count / total_trades if total_trades > 0 else 0.0
        
        # Average win/loss
        avg_win = (sum(p.pnl for p in winning_trades) / winning_count) if winning_trades else 0.0
        avg_loss = (sum(p.pnl for p in losing_trades) / losing_count) if losing_trades else 0.0
        
        # Profit factor
        total_profit = sum([p.pnl for p in winning_trades]) if winning_trades else 0.0
        total_loss = abs(sum([p.pnl for p in losing_trades])) if losing_trades else 0.0
        profit_factor = total_profit / total_loss if total_loss > 0 else float('inf') if total_profit > 0 else 0.0
        
        # Max drawdown
        running_peak = float("-inf")
        max_drawdown = 0.0
        for eq in equity_curve:
            eq_val = float(eq)
            if eq_val > running_peak:
                running_peak = eq_val
            drawdown = running_peak - eq_val
            if drawdown > max_drawdown:
                max_drawdown = drawdown
        max_drawdown_pct = max_drawdown / initial_capital if initial_capital > 0 else 0.0
        
        # Sharpe ratio (simplified - using equity curve returns, population std dev)
        returns: List[float] = []
        for i in range(1, len(equity_curve)):
            prev = float(equity_curve[i - 1])
            curr = float(equity_curve[i])
            if prev != 0:
                returns.append((curr - prev) / prev)

        if len(returns) > 1:
            mean_ret = sum(returns) / len(returns)
            variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
            std_ret = math.sqrt(variance)
            # Annualise using the bar spacing of `data` (252 for daily,
            # 84 for 3d, etc.) — see _infer_periods_per_year.
            sharpe_ratio = (mean_ret / std_ret) * math.sqrt(periods_per_year) if std_ret > 0 else 0.0
        else:
            sharpe_ratio = 0.0
        
        # Total return
        total_return = final_capital - initial_capital
        
        return BacktestResult(
            total_return=total_return,
            total_trades=total_trades,
            winning_trades=winning_count,
            losing_trades=losing_count,
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            profit_factor=profit_factor,
            max_drawdown=max_drawdown,
            max_drawdown_pct=max_drawdown_pct,
            sharpe_ratio=sharpe_ratio,
            equity_curve=equity_curve if isinstance(equity_curve, list) else equity_curve.tolist(),
            trades=positions,
            initial_capital=initial_capital,
            final_capital=final_capital
        )
