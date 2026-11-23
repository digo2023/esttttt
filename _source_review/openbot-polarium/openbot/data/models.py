"""Data models for openbot-polarium v2.0"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Dict


class Timeframe(str, Enum):
    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"

    @property
    def seconds(self) -> int:
        return {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600}[self.value]

    @property
    def quadcode_id(self) -> int:
        return {60: 1, 300: 2, 900: 3, 1800: 4, 3600: 5}[self.seconds]


class Direction(str, Enum):
    CALL = "CALL"
    PUT = "PUT"
    HOLD = "HOLD"


class TradeMode(str, Enum):
    PAPER = "paper"
    REAL = "real"


class TradeStatus(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    WON = "won"
    LOST = "lost"
    CANCELLED = "cancelled"


@dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: int
    closed: bool = True

    @property
    def datetime(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc)

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range_(self) -> float:
        return self.high - self.low

    @property
    def is_bull(self) -> bool:
        return self.close > self.open

    @property
    def is_bear(self) -> bool:
        return self.close < self.open

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Asset:
    id: int
    name: str
    symbol: str
    payout: float
    is_otc: bool = False
    active: bool = True
    min_stake: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Signal:
    direction: Direction
    confidence: float
    timeframe: Timeframe
    asset: str
    timestamp: int
    entry_price: float
    expiry_seconds: int
    indicators_snapshot: Dict = field(default_factory=dict)
    patterns_detected: List[str] = field(default_factory=list)
    strategy_votes: Dict[str, float] = field(default_factory=dict)
    llm_reasoning: str = ""
    llm_raw_response: Optional[Dict] = None
    # New: which indicators contributed most to this signal
    top_contributors: List[str] = field(default_factory=list)

    @property
    def is_actionable(self) -> bool:
        return self.direction != Direction.HOLD and self.confidence >= 0.55

    def to_dict(self) -> dict:
        d = asdict(self)
        d["direction"] = self.direction.value
        d["timeframe"] = self.timeframe.value
        return d


@dataclass
class Trade:
    """A trade that was actually opened (paper or real)."""
    id: str
    signal: Signal
    stake: float
    payout_pct: float
    gale_level: int = 0
    status: TradeStatus = TradeStatus.PENDING
    entry_price: float = 0.0
    exit_price: Optional[float] = None
    open_timestamp: int = 0
    close_timestamp: Optional[int] = None
    won: Optional[bool] = None
    pnl: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        d["signal"] = self.signal.to_dict()
        return d


@dataclass
class IndicatorPerformance:
    """Tracks accuracy of each indicator/strategy over recent trades."""
    name: str
    correct: int = 0
    wrong: int = 0
    total: int = 0
    weight: float = 1.0
    last_updated: int = 0

    @property
    def accuracy(self) -> float:
        return self.correct / max(self.total, 1)

    def record(self, was_correct: bool) -> None:
        self.total += 1
        if was_correct:
            self.correct += 1
        else:
            self.wrong += 1
        import time
        self.last_updated = int(time.time())

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "correct": self.correct,
            "wrong": self.wrong,
            "total": self.total,
            "accuracy": round(self.accuracy, 3),
            "weight": round(self.weight, 4),
        }


@dataclass
class ScannerResult:
    """Result of scanning multiple asset/timeframe combos."""
    timestamp: int
    signals: List[Signal] = field(default_factory=list)

    @property
    def top_signals(self) -> List[Signal]:
        """Sorted by confidence descending, only actionable."""
        actionable = [s for s in self.signals if s.is_actionable]
        return sorted(actionable, key=lambda s: -s.confidence)

    @property
    def best(self) -> Optional[Signal]:
        return self.top_signals[0] if self.top_signals else None


@dataclass
class Session:
    """Active trading session state."""
    started_at: int
    mode: TradeMode
    account_type: str
    initial_balance: float
    current_balance: float
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

    def to_dict(self) -> dict:
        return asdict(self)
