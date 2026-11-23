"""
Learning engine — online weighting of indicators/strategies.

Tracks accuracy of each signal source over recent trades and adjusts
weights dynamically. After a loss, revalidates which indicators are
currently performing best.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

from ..data.models import IndicatorPerformance

log = logging.getLogger(__name__)


class LearningEngine:
    """
    Persists indicator/strategy performance to SQLite (via JSON file fallback).
    Each completed trade updates the contributing indicators' correct/wrong counts.
    """

    def __init__(
        self,
        db_path: str = "openbot_state.json",
        window: int = 50,
        learning_rate: float = 0.1,
        min_weight: float = 0.05,
        top_indicators: int = 8,
        revalidate_after_loss: bool = True,
    ) -> None:
        self.db_path = db_path
        self.window = window
        self.learning_rate = learning_rate
        self.min_weight = min_weight
        self.top_indicators = top_indicators
        self.revalidate_after_loss = revalidate_after_loss
        self._performances: Dict[str, IndicatorPerformance] = {}
        self._lock = asyncio.Lock()
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.db_path):
            return
        try:
            with open(self.db_path) as f:
                data = json.load(f)
            for name, perf in data.items():
                self._performances[name] = IndicatorPerformance(
                    name=name,
                    correct=perf.get("correct", 0),
                    wrong=perf.get("wrong", 0),
                    total=perf.get("total", 0),
                    weight=perf.get("weight", 1.0),
                    last_updated=perf.get("last_updated", 0),
                )
            log.info("Loaded %d indicator performance records", len(self._performances))
        except Exception as e:
            log.warning("Failed to load learning state: %s", e)

    def _save(self) -> None:
        try:
            data = {name: p.to_dict() for name, p in self._performances.items()}
            with open(self.db_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.warning("Failed to save learning state: %s", e)

    def get_or_create(self, name: str) -> IndicatorPerformance:
        if name not in self._performances:
            self._performances[name] = IndicatorPerformance(name=name)
        return self._performances[name]

    def get_weight(self, name: str) -> float:
        return self.get_or_create(name).weight

    def get_weights(self, names: List[str]) -> Dict[str, float]:
        return {n: self.get_weight(n) for n in names}

    async def record_trade(
        self,
        contributors: List[str],
        won: bool,
    ) -> None:
        """Update performance of contributors based on trade outcome."""
        async with self._lock:
            for name in contributors:
                perf = self.get_or_create(name)
                perf.record(was_correct=won)

            # Adjust weights based on accuracy
            await self._recalculate_weights()

            if not won and self.revalidate_after_loss:
                await self._revalidate_top()

            self._save()

    async def _recalculate_weights(self) -> None:
        """Adjust weights based on recent accuracy."""
        for name, perf in self._performances.items():
            # Use only recent window
            if perf.total == 0:
                continue
            recent_total = min(perf.total, self.window)
            recent_correct = int(perf.accuracy * recent_total)
            accuracy = recent_correct / max(recent_total, 1)
            # Weight = accuracy normalized, with learning_rate step
            target_weight = max(self.min_weight, accuracy)
            # Exponential moving update
            perf.weight = (1 - self.learning_rate) * perf.weight + self.learning_rate * target_weight

    async def _revalidate_top(self) -> None:
        """Keep only top N indicators above min_weight; reset others."""
        sorted_perfs = sorted(self._performances.values(), key=lambda p: -p.accuracy)
        for i, perf in enumerate(sorted_perfs):
            if i < self.top_indicators:
                # Boost top performers slightly
                perf.weight = min(1.0, perf.weight * 1.05)
            else:
                # Demote the rest
                perf.weight = max(self.min_weight, perf.weight * 0.9)

    def top_performers(self, n: int = 5) -> List[Dict]:
        """Return top N performing indicators/strategies by accuracy."""
        sorted_perfs = sorted(self._performances.values(), key=lambda p: -p.accuracy)
        return [
            {
                "name": p.name,
                "accuracy": round(p.accuracy * 100, 2),
                "total": p.total,
                "weight": round(p.weight, 3),
            }
            for p in sorted_perfs[:n] if p.total > 0
        ]

    def status(self) -> Dict:
        return {
            "total_indicators_tracked": len(self._performances),
            "top_performers": self.top_performers(10),
            "settings": {
                "window": self.window,
                "learning_rate": self.learning_rate,
                "min_weight": self.min_weight,
                "top_indicators": self.top_indicators,
                "revalidate_after_loss": self.revalidate_after_loss,
            },
        }
