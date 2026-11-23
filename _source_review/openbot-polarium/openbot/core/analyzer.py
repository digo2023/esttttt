"""
Ensemble analyzer — combines ALL indicators/strategies with dynamic
weights from the learning engine, then optionally consults the LLM
for final confirmation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Dict, List, Optional, Tuple

from ..data.models import Candle, Direction, Signal, Timeframe
from ..indicators.technical import compute_indicators
from ..indicators.patterns import detect_patterns, net_pattern_signal
from ..indicators.price_action import price_action_snapshot
from .apinex_client import APInexClient, APInexError
from .learning import LearningEngine

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a senior binary options analyst specialized in M1/M5 timeframes.
You receive recent OHLCV candles + 15+ indicators + candlestick patterns + price action context
+ an aggregated classical signal with weighted votes from each indicator.

Your task: predict the NEXT candle direction (CALL or PUT) with calibrated confidence.
Be honest about uncertainty. Return STRICT JSON only:

{
  "direction": "CALL" | "PUT" | "HOLD",
  "confidence": 0.0-1.0,
  "reasoning": "2-3 short sentences explaining the call",
  "key_factors": ["factor1", "factor2", "factor3"],
  "agree_with_classical": true | false
}

Rules:
- CALL when strong bullish confluence (trend up + RSI < 70 + bullish patterns + price at support/BB lower).
- PUT when strong bearish confluence (trend down + RSI > 30 + bearish patterns + price at resistance/BB upper).
- HOLD when signals conflict or confidence < 0.55.
- Confidence reflects confluence strength, NOT probability of profit.
"""


class EnsembleAnalyzer:
    """Combines classical analysis (weighted by learning) + LLM confirmation."""

    def __init__(
        self,
        apinex: Optional[APInexClient] = None,
        learning: Optional[LearningEngine] = None,
        model: str = "gpt-4o-mini",
        temperature: float = 0.3,
        max_tokens: int = 600,
        context_candles: int = 30,
        ai_enabled: bool = True,
    ) -> None:
        self.apinex = apinex
        self.learning = learning or LearningEngine()
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.context_candles = context_candles
        self.ai_enabled = ai_enabled and apinex is not None

    async def analyze(
        self,
        candles: List[Candle],
        asset: str,
        timeframe: Timeframe,
    ) -> Signal:
        """Full analysis pipeline: indicators → patterns → price action → weighted vote → LLM."""
        # 1. Compute all indicators (returns votes already)
        indicators = compute_indicators(candles)
        if "_error" in indicators:
            return self._hold_signal(asset, timeframe, candles[-1] if candles else None, indicators, [], {}, "")

        # 2. Patterns
        patterns = detect_patterns(candles)
        pattern_dir, pattern_conf = net_pattern_signal(patterns)

        # 3. Price action
        pa = price_action_snapshot(candles)
        if "_error" in pa:
            pa = {}

        # 4. Aggregate votes with LEARNED weights
        votes = indicators.get("votes", {})
        bull_score, bear_score, contributors = self._weighted_vote(votes, pa, pattern_dir, pattern_conf)

        # 5. Classical signal
        classical_dir, classical_conf = self._aggregate(bull_score, bear_score)

        # 6. LLM confirmation (if enabled)
        llm_reasoning = ""
        llm_raw = None
        final_dir = classical_dir
        final_conf = classical_conf

        if self.ai_enabled and self.apinex is not None:
            try:
                llm_raw = await self._call_llm(candles, indicators, patterns, pa, classical_dir, classical_conf, asset, timeframe)
                llm_dir_str = str(llm_raw.get("direction", "HOLD")).upper()
                try:
                    llm_dir = Direction(llm_dir_str)
                except ValueError:
                    llm_dir = Direction.HOLD
                llm_conf = float(llm_raw.get("confidence", 0.5))
                llm_conf = max(0.0, min(1.0, llm_conf))
                llm_reasoning = llm_raw.get("reasoning", "")

                # Fusion rules
                if llm_dir == classical_dir and llm_dir != Direction.HOLD:
                    # Agreement: boost confidence
                    final_dir = llm_dir
                    final_conf = min(1.0, 0.5 * llm_conf + 0.5 * classical_conf + 0.05)
                elif llm_dir != classical_dir and llm_dir != Direction.HOLD:
                    # Disagreement: lower confidence
                    final_dir = llm_dir if llm_conf > classical_conf + 0.1 else classical_dir
                    final_conf = 0.3 * llm_conf + 0.3 * classical_conf
                else:
                    # LLM says HOLD
                    final_dir = Direction.HOLD
                    final_conf = 0.5 * llm_conf

                # Add LLM as a "vote contributor" so it gets tracked too
                contributors.append("llm")
                votes["llm"] = (llm_dir.value.lower() if llm_dir != Direction.HOLD else "neutral", llm_conf)
            except Exception as e:
                log.warning("LLM call failed: %s — using classical only", e)
                llm_reasoning = f"LLM unavailable, classical only. Error: {e}"

        # Top contributors (highest weighted votes)
        top_contributors = self._top_contributors(votes, contributors)

        return Signal(
            direction=final_dir,
            confidence=round(final_conf, 3),
            timeframe=timeframe,
            asset=asset,
            timestamp=int(time.time()),
            entry_price=candles[-1].close,
            expiry_seconds=timeframe.seconds,
            indicators_snapshot={k: v for k, v in indicators.items() if not isinstance(v, dict)},
            patterns_detected=[p["name"] for p in patterns],
            strategy_votes={name: round(v[1], 3) for name, v in votes.items()},
            llm_reasoning=llm_reasoning,
            llm_raw_response=llm_raw,
            top_contributors=top_contributors,
        )

    def _weighted_vote(self, votes: Dict, pa: Dict, pattern_dir: str, pattern_conf: float) -> Tuple[float, float, List[str]]:
        """Aggregate votes weighted by learning engine."""
        bull = 0.0
        bear = 0.0
        contributors = []

        for name, (direction, base_conf) in votes.items():
            weight = self.learning.get_weight(name)
            contribution = base_conf * weight
            if direction == "bullish":
                bull += contribution
                contributors.append(name)
            elif direction == "bearish":
                bear += contribution
                contributors.append(name)

        # Price action votes (not in indicators)
        if pa:
            trend = pa.get("trend")
            trend_conf = pa.get("trend_confidence", 0)
            weight = self.learning.get_weight("pa_trend")
            if trend == "bullish":
                bull += trend_conf * weight
                contributors.append("pa_trend")
            elif trend == "bearish":
                bear += trend_conf * weight
                contributors.append("pa_trend")
            breakout = pa.get("breakout")
            if breakout == "bullish_breakout":
                bull += 0.35 * self.learning.get_weight("pa_breakout")
                contributors.append("pa_breakout")
            elif breakout == "bearish_breakout":
                bear += 0.35 * self.learning.get_weight("pa_breakout")
                contributors.append("pa_breakout")
            if pa.get("volume_state") == "high":
                if bull > bear:
                    bull += 0.15 * self.learning.get_weight("pa_volume")
                else:
                    bear += 0.15 * self.learning.get_weight("pa_volume")
                contributors.append("pa_volume")

        # Pattern votes
        if pattern_dir == "bullish":
            bull += pattern_conf * 0.5 * self.learning.get_weight("patterns")
            contributors.append("patterns")
        elif pattern_dir == "bearish":
            bear += pattern_conf * 0.5 * self.learning.get_weight("patterns")
            contributors.append("patterns")

        return bull, bear, contributors

    def _aggregate(self, bull: float, bear: float) -> Tuple[Direction, float]:
        if bull == 0 and bear == 0:
            return Direction.HOLD, 0.0
        if bull > bear:
            return Direction.CALL, round(bull / (bull + bear + 0.5), 3)
        if bear > bull:
            return Direction.PUT, round(bear / (bull + bear + 0.5), 3)
        return Direction.HOLD, 0.5

    def _top_contributors(self, votes: Dict, contributors: List[str], top_n: int = 5) -> List[str]:
        scored = []
        for name in contributors:
            if name in votes:
                _, conf = votes[name]
                weight = self.learning.get_weight(name)
                scored.append((name, conf * weight))
        scored.sort(key=lambda x: -x[1])
        return [name for name, _ in scored[:top_n]]

    async def _call_llm(self, candles, indicators, patterns, pa, classical_dir, classical_conf, asset, timeframe):
        recent = candles[-self.context_candles:]
        candle_data = [
            {
                "t": c.timestamp, "o": round(c.open, 6), "h": round(c.high, 6),
                "l": round(c.low, 6), "c": round(c.close, 6), "v": round(c.volume, 2),
            } for c in recent
        ]
        prompt = json.dumps({
            "asset": asset, "timeframe": timeframe.value,
            "current_price": candles[-1].close,
            "recent_candles": candle_data,
            "indicators": {k: v for k, v in indicators.items() if not isinstance(v, dict)},
            "patterns_detected": patterns,
            "price_action": pa,
            "classical_signal": {
                "direction": classical_dir.value,
                "confidence": classical_conf,
                "top_contributors": indicators.get("votes", {}),
            },
        }, indent=2)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        return await self.apinex.chat_json(
            messages, model=self.model, temperature=self.temperature, max_tokens=self.max_tokens
        )

    def _hold_signal(self, asset, timeframe, last_candle, indicators, patterns, votes, reasoning):
        return Signal(
            direction=Direction.HOLD,
            confidence=0.0,
            timeframe=timeframe, asset=asset, timestamp=int(time.time()),
            entry_price=last_candle.close if last_candle else 0.0,
            expiry_seconds=timeframe.seconds,
            indicators_snapshot={k: v for k, v in indicators.items() if not isinstance(v, dict)},
            patterns_detected=[p.get("name", "") for p in patterns],
            strategy_votes={},
            llm_reasoning=reasoning or "HOLD — insufficient data",
            top_contributors=[],
        )
