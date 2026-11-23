"""
Bankroll management — v2 with stop win/loss, entry value, gale, soros.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..data.models import TradeMode


@dataclass
class BankrollConfig:
    initial_balance: float = 1000.0
    entry_value: float = 20.0      # FIXED stake (v2 — was risk_percent before)
    stop_win_pct: float = 30.0
    stop_loss_pct: float = 20.0
    max_trades_per_day: int = 20
    max_concurrent_trades: int = 1
    martingale_enabled: bool = True
    martingale_levels: int = 2
    martingale_multiplier: float = 2.0
    soros_enabled: bool = False
    soros_levels: int = 1


@dataclass
class BankrollManager:
    cfg: BankrollConfig
    mode: TradeMode = TradeMode.PAPER
    balance: float = field(init=False)
    starting_balance: float = field(init=False)
    trades_today: int = 0
    wins_today: int = 0
    losses_today: int = 0
    pnl_today: float = 0.0
    consecutive_losses: int = 0
    consecutive_wins: int = 0
    gale_level: int = 0
    last_stake: float = 0.0
    stopped: bool = False
    stop_reason: Optional[str] = None

    def __post_init__(self):
        self.balance = self.cfg.initial_balance
        self.starting_balance = self.cfg.initial_balance

    def can_trade(self) -> bool:
        """Check if we can open a new trade."""
        if self.stopped:
            return False
        if self.trades_today >= self.cfg.max_trades_per_day:
            self.stopped = True
            self.stop_reason = f"Max trades per day reached ({self.cfg.max_trades_per_day})"
            return False
        # Stop loss check
        loss_pct = abs(min(0, self.pnl_today)) / self.starting_balance * 100
        if self.pnl_today < 0 and loss_pct >= self.cfg.stop_loss_pct:
            self.stopped = True
            self.stop_reason = f"Stop Loss reached (-{self.cfg.stop_loss_pct}%)"
            return False
        # Stop win check
        win_pct = max(0, self.pnl_today) / self.starting_balance * 100
        if self.pnl_today > 0 and win_pct >= self.cfg.stop_win_pct:
            self.stopped = True
            self.stop_reason = f"Stop Win reached (+{self.cfg.stop_win_pct}%)"
            return False
        if self.balance < self.cfg.entry_value:
            self.stopped = True
            self.stop_reason = "Insufficient balance"
            return False
        return True

    def next_stake(self, payout_pct: float = 87.0) -> float:
        """Calculate the next stake based on entry_value + martingale."""
        if not self.can_trade():
            return 0.0

        base = self.cfg.entry_value

        if self.cfg.martingale_enabled and self.gale_level > 0:
            stake = base * (self.cfg.martingale_multiplier ** self.gale_level)
        elif self.cfg.soros_enabled and self.consecutive_wins > 0:
            levels = min(self.consecutive_wins, self.cfg.soros_levels)
            stake = base * (1 + 0.5 * levels)
        else:
            stake = base

        stake = min(stake, self.balance)
        stake = max(1.0, round(stake, 2))
        self.last_stake = stake
        return stake

    def record_trade(self, won: bool, stake: float, payout_pct: float = 87.0):
        """Record a completed trade."""
        if won:
            profit = stake * (payout_pct / 100.0)
            self.balance += profit
            self.wins_today += 1
            self.consecutive_wins += 1
            self.consecutive_losses = 0
            self.gale_level = 0  # reset gale on win
        else:
            profit = -stake
            self.balance += profit
            self.losses_today += 1
            self.consecutive_losses += 1
            self.consecutive_wins = 0
            if self.cfg.martingale_enabled and self.gale_level < self.cfg.martingale_levels:
                self.gale_level += 1
            else:
                self.gale_level = 0  # reset if max gale reached

        self.trades_today += 1
        self.pnl_today = self.balance - self.starting_balance

        # Re-check stops
        self.can_trade()

        return {
            "won": won,
            "stake": stake,
            "pnl": round(profit, 2),
            "new_balance": round(self.balance, 2),
            "gale_level": self.gale_level,
            "pnl_today": round(self.pnl_today, 2),
            "pnl_today_pct": round(self.pnl_today / self.starting_balance * 100, 2),
        }

    def reset_day(self):
        """Reset daily counters."""
        self.starting_balance = self.balance
        self.trades_today = 0
        self.wins_today = 0
        self.losses_today = 0
        self.pnl_today = 0.0
        self.stopped = False
        self.stop_reason = None

    def status(self) -> dict:
        return {
            "mode": self.mode.value,
            "balance": round(self.balance, 2),
            "starting_balance": round(self.starting_balance, 2),
            "pnl_today": round(self.pnl_today, 2),
            "pnl_today_pct": round(self.pnl_today / max(self.starting_balance, 0.01) * 100, 2),
            "trades_today": self.trades_today,
            "wins": self.wins_today,
            "losses": self.losses_today,
            "win_rate": round(self.wins_today / max(self.trades_today, 1) * 100, 2),
            "consecutive_losses": self.consecutive_losses,
            "consecutive_wins": self.consecutive_wins,
            "gale_level": self.gale_level,
            "next_stake": round(self.next_stake(), 2),
            "stopped": self.stopped,
            "stop_reason": self.stop_reason,
            "stop_win_pct": self.cfg.stop_win_pct,
            "stop_loss_pct": self.cfg.stop_loss_pct,
        }
