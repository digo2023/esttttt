"""
Auto-executor — opens real or paper trades based on signals.

Safety: only opens real trades when AUTO_EXECUTE=true AND mode=real.
Otherwise simulates in paper mode.
"""

from __future__ import annotations

import asyncio
import csv
import logging
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional

from ..core.learning import LearningEngine
from ..data.models import Direction, Signal, Trade, TradeMode, TradeStatus
from ..data.polarium_client import PolariumClient
from .bankroll import BankrollManager

log = logging.getLogger(__name__)


class AutoExecutor:
    """Executes (or simulates) trades based on signals."""

    def __init__(
        self,
        polarium: PolariumClient,
        bankroll: BankrollManager,
        learning: LearningEngine,
        mode: TradeMode = TradeMode.PAPER,
        auto_execute: bool = False,
        min_confidence_to_execute: float = 0.70,
        payout_pct: float = 87.0,
        trades_csv: str = "logs/trades.csv",
    ) -> None:
        self.polarium = polarium
        self.bankroll = bankroll
        self.learning = learning
        self.mode = mode
        self.auto_execute = auto_execute
        self.min_confidence_to_execute = min_confidence_to_execute
        self.payout_pct = payout_pct
        self.trades_csv = trades_csv
        self.open_trades: List[Trade] = []
        self.completed_trades: List[Trade] = []

    def should_execute(self, signal: Signal) -> bool:
        """Decide whether to execute this signal."""
        if not self.bankroll.can_trade():
            return False
        if signal.direction == Direction.HOLD:
            return False
        if signal.confidence < self.min_confidence_to_execute:
            return False
        if self.mode == TradeMode.REAL and not self.auto_execute:
            log.warning("REAL mode but auto_execute=False — skipping")
            return False
        return True

    async def execute(self, signal: Signal) -> Optional[Trade]:
        """Open a trade based on the signal."""
        if not self.should_execute(signal):
            return None

        stake = self.bankroll.next_stake(self.payout_pct)
        if stake <= 0:
            return None

        # Decide if paper or real
        is_practice = self.mode == TradeMode.PAPER or not self.auto_execute

        trade_id = str(uuid.uuid4())[:8]
        trade = Trade(
            id=trade_id,
            signal=signal,
            stake=stake,
            payout_pct=self.payout_pct,
            gale_level=self.bankroll.gale_level,
            status=TradeStatus.OPEN,
            entry_price=signal.entry_price,
            open_timestamp=int(time.time()),
        )

        if not is_practice:
            # Place real order on Polarium
            try:
                result = await self.polarium.place_binary_option(
                    asset=signal.asset,
                    direction=signal.direction.value.lower(),
                    amount=stake,
                    expiry_seconds=signal.expiry_seconds,
                    is_practice=False,
                )
                if result.get("_error"):
                    log.error("Order failed: %s", result.get("_error"))
                    return None
                log.info("REAL ORDER placed: %s %s %s $%.2f",
                         trade_id, signal.direction.value, signal.asset, stake)
            except Exception as e:
                log.error("Order placement failed: %s", e)
                return None
        else:
            log.info("PAPER ORDER: %s %s %s $%.2f (conf=%.2f)",
                     trade_id, signal.direction.value, signal.asset, stake, signal.confidence)

        self.open_trades.append(trade)
        return trade

    async def settle_trade(self, trade: Trade, exit_price: float) -> None:
        """Settle an open trade when expiry is reached."""
        won = (
            (trade.signal.direction == Direction.CALL and exit_price > trade.entry_price)
            or (trade.signal.direction == Direction.PUT and exit_price < trade.entry_price)
        )
        trade.exit_price = exit_price
        trade.close_timestamp = int(time.time())
        trade.won = won
        trade.status = TradeStatus.WON if won else TradeStatus.LOST

        result = self.bankroll.record_trade(won, trade.stake, self.payout_pct)
        trade.pnl = result["pnl"]

        # Update learning engine with contributors
        await self.learning.record_trade(trade.signal.top_contributors, won)

        self.open_trades.remove(trade)
        self.completed_trades.append(trade)

        log.info("TRADE SETTLED: %s %s entry=%.5f exit=%.5f won=%s pnl=%.2f balance=%.2f",
                 trade.id, trade.signal.direction.value,
                 trade.entry_price, exit_price, won, trade.pnl,
                 self.bankroll.balance)

        # Log to CSV
        self._log_trade(trade)

    def _log_trade(self, trade: Trade) -> None:
        Path(self.trades_csv).parent.mkdir(parents=True, exist_ok=True)
        row = {
            "id": trade.id,
            "asset": trade.signal.asset,
            "timeframe": trade.signal.timeframe.value,
            "direction": trade.signal.direction.value,
            "confidence": trade.signal.confidence,
            "entry_price": trade.entry_price,
            "exit_price": trade.exit_price,
            "stake": trade.stake,
            "won": trade.won,
            "pnl": trade.pnl,
            "gale_level": trade.gale_level,
            "open_ts": trade.open_timestamp,
            "close_ts": trade.close_timestamp,
            "top_contributors": ",".join(trade.signal.top_contributors),
            "reasoning": trade.signal.llm_reasoning[:200],
        }
        file_exists = Path(self.trades_csv).exists()
        with open(self.trades_csv, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(row.keys()))
            if not file_exists:
                w.writeheader()
            w.writerow(row)

    async def check_pending_settlements(self) -> None:
        """Check open trades that have passed their expiry."""
        now = int(time.time())
        to_settle = []
        for t in self.open_trades:
            if now >= t.open_timestamp + t.signal.expiry_seconds:
                # Fetch latest candle to determine exit price
                try:
                    candles = await self.polarium.get_candles(
                        t.signal.asset, t.signal.timeframe, 2
                    )
                    if candles:
                        to_settle.append((t, candles[-1].close))
                except Exception as e:
                    log.warning("Failed to fetch exit candle for %s: %s", t.id, e)

        for trade, exit_price in to_settle:
            await self.settle_trade(trade, exit_price)

    def status(self) -> dict:
        return {
            "mode": self.mode.value,
            "auto_execute": self.auto_execute,
            "open_trades": len(self.open_trades),
            "completed_trades": len(self.completed_trades),
            "min_confidence_to_execute": self.min_confidence_to_execute,
            "payout_pct": self.payout_pct,
        }
