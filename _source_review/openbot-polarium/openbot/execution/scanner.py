"""
Scanner — iterates multiple assets × timeframes in parallel,
collects signals, ranks by confidence.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import List, Optional, Tuple

from ..core.analyzer import EnsembleAnalyzer
from ..core.self_healing import with_retry
from ..data.models import Candle, Direction, Signal, Timeframe
from ..data.polarium_client import PolariumClient

log = logging.getLogger(__name__)


class Scanner:
    """Scans multiple asset/timeframe combos and returns ranked signals."""

    def __init__(
        self,
        polarium: PolariumClient,
        analyzer: EnsembleAnalyzer,
        candle_history: int = 200,
        parallel: int = 3,
    ) -> None:
        self.polarium = polarium
        self.analyzer = analyzer
        self.candle_history = candle_history
        self.parallel = parallel
        self._semaphore = asyncio.Semaphore(parallel)

    async def scan(
        self,
        assets: List[str],
        timeframes: List[Timeframe],
        min_confidence: float = 0.55,
    ) -> List[Signal]:
        """Scan all asset × timeframe combos, return sorted signals."""
        tasks = []
        for asset in assets:
            for tf in timeframes:
                tasks.append(self._scan_one(asset, tf))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        signals = []
        for r in results:
            if isinstance(r, Signal):
                if r.confidence >= min_confidence or r.direction != Direction.HOLD:
                    signals.append(r)
            elif isinstance(r, Exception):
                log.warning("Scan task failed: %s", r)

        # Sort: actionable first (CALL/PUT), then by confidence desc
        signals.sort(key=lambda s: (s.direction == Direction.HOLD, -s.confidence))
        return signals

    @with_retry(max_attempts=2, base_delay=1.0, exceptions=(Exception,))
    async def _scan_one(self, asset: str, tf: Timeframe) -> Signal:
        async with self._semaphore:
            candles = await self.polarium.get_candles(asset, tf, self.candle_history)
            if len(candles) < 30:
                log.debug("Not enough candles for %s %s (got %d)", asset, tf, len(candles))
                # Return HOLD signal
                return Signal(
                    direction=Direction.HOLD, confidence=0.0,
                    timeframe=tf, asset=asset, timestamp=int(time.time()),
                    entry_price=0.0, expiry_seconds=tf.seconds,
                    llm_reasoning=f"Insufficient candles ({len(candles)})",
                )
            return await self.analyzer.analyze(candles, asset, tf)
