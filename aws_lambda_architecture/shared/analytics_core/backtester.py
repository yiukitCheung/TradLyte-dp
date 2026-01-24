"""
Backtester Engine

Simulates strategy execution with position tracking and performance metrics.
Supports stop loss, take profit, trailing stops, and calculates comprehensive metrics.
"""

import polars as pl
from typing import List, Dict, Any, Optional
from datetime import date, datetime
from dataclasses import dataclass
import numpy as np
from .strategies.base import BaseStrategy
from .executor import MultiTimeframeExecutor


@dataclass
class Position:
    """Represents a trading position"""
    entry_date: date
    entry_price: float
    exit_date: Optional[date] = None
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    holding_days: Optional[int] = None
    exit_reason: Optional[str] = None  # 'stop_loss', 'take_profit', 'signal', 'trailing_stop', 'time_based'
    
    def is_open(self) -> bool:
        """Check if position is still open"""
        return self.exit_date is None
    
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
        # Execute strategy to get signals
        if 'signal' not in data.columns:
            data = strategy.run(data)
        
        # Initialize tracking
        positions: List[Position] = []
        open_position: Optional[Position] = None
        equity_curve: List[float] = [self.initial_capital]
        capital = self.initial_capital
        
        # Sort by date
        data = data.sort('date')
        
        # Iterate through data
        for row in data.iter_rows(named=True):
            current_date = row['date']
            current_price = row['close']
            signal = row.get('signal', 'HOLD')
            
            # Check for exit conditions on open position
            if open_position is not None:
                should_exit, exit_reason = self._check_exit_conditions(
                    open_position,
                    current_date,
                    current_price,
                    row,
                    stop_loss_pct,
                    take_profit_pct,
                    trailing_stop_pct,
                    max_holding_days
                )
                
                if should_exit:
                    # Apply slippage
                    exit_price = current_price * (1 - self.slippage) if self.slippage > 0 else current_price
                    open_position.close(current_date, exit_price, exit_reason)
                    
                    # Update capital
                    pnl = open_position.pnl * (capital / open_position.entry_price)  # Scale P&L
                    capital += pnl - (self.commission * 2)  # Entry + exit commission
                    
                    positions.append(open_position)
                    open_position = None
                    equity_curve.append(capital)
            
            # Check for entry signal
            if signal == 'BUY' and open_position is None:
                # Apply slippage
                entry_price = current_price * (1 + self.slippage) if self.slippage > 0 else current_price
                
                # Create new position
                open_position = Position(
                    entry_date=current_date,
                    entry_price=entry_price
                )
                
                # Deduct commission
                capital -= self.commission
                equity_curve.append(capital)
            
            # Update equity curve even if no trade
            if open_position is None:
                equity_curve.append(capital)
        
        # Close any remaining open position at end
        if open_position is not None:
            last_row = data.tail(1).iter_rows(named=True).__next__()
            exit_price = last_row['close'] * (1 - self.slippage) if self.slippage > 0 else last_row['close']
            open_position.close(last_row['date'], exit_price, 'end_of_data')
            pnl = open_position.pnl * (capital / open_position.entry_price)
            capital += pnl - self.commission
            positions.append(open_position)
            equity_curve.append(capital)
        
        # Calculate metrics
        return self._calculate_metrics(positions, equity_curve, self.initial_capital, capital)
    
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
        
        # Check trailing stop (simplified - tracks highest price since entry)
        if trailing_stop_pct:
            # This is simplified - in practice, you'd track highest price since entry
            # For now, use current price as proxy
            trailing_stop_price = current_price * (1 - trailing_stop_pct)
            if current_price < trailing_stop_price:
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
        final_capital: float
    ) -> BacktestResult:
        """Calculate performance metrics"""
        
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
        avg_win = np.mean([p.pnl for p in winning_trades]) if winning_trades else 0.0
        avg_loss = np.mean([p.pnl for p in losing_trades]) if losing_trades else 0.0
        
        # Profit factor
        total_profit = sum([p.pnl for p in winning_trades]) if winning_trades else 0.0
        total_loss = abs(sum([p.pnl for p in losing_trades])) if losing_trades else 0.0
        profit_factor = total_profit / total_loss if total_loss > 0 else float('inf') if total_profit > 0 else 0.0
        
        # Max drawdown
        equity_array = np.array(equity_curve)
        running_max = np.maximum.accumulate(equity_array)
        drawdown = equity_array - running_max
        max_drawdown = abs(np.min(drawdown))
        max_drawdown_pct = max_drawdown / initial_capital if initial_capital > 0 else 0.0
        
        # Sharpe ratio (simplified - using equity curve returns)
        returns = np.diff(equity_array) / equity_array[:-1]
        if len(returns) > 1 and np.std(returns) > 0:
            sharpe_ratio = np.mean(returns) / np.std(returns) * np.sqrt(252)  # Annualized
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
