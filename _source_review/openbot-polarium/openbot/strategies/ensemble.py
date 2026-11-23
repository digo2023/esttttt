"""
Strategy ensemble — 8 named strategies, each produces a vote.

Each strategy is independent; the analyzer weights them by recent accuracy.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from ..data.models import Candle, Direction
from ..indicators.technical import (
    rsi, macd, bollinger_bands, ema, sma, stochastic, atr, adx,
    ichimoku, parabolic_sar, obv, vwap, williams_r, cci,
    fibonacci_levels, atr_bands,
)
from ..indicators.patterns import detect_patterns, net_pattern_signal
from ..indicators.price_action import price_action_snapshot


class TrendFollowingStrategy:
    """EMA cross + ADX filter — classic trend riding."""
    name = "trend_following"

    def evaluate(self, candles: List[Candle]) -> Tuple[str, float]:
        if len(candles) < 30:
            return "neutral", 0.0
        closes = [c.close for c in candles]
        ema_fast = ema(closes, 9)
        ema_slow = ema(closes, 21)
        adx_val, plus_di, minus_di = adx(
            [c.high for c in candles], [c.low for c in candles], closes
        )
        if adx_val[-1] < 20:
            return "neutral", 0.2
        if ema_fast[-1] > ema_slow[-1] and plus_di[-1] > minus_di[-1]:
            return "bullish", min(1.0, 0.5 + (adx_val[-1] - 20) * 0.01)
        if ema_fast[-1] < ema_slow[-1] and minus_di[-1] > plus_di[-1]:
            return "bearish", min(1.0, 0.5 + (adx_val[-1] - 20) * 0.01)
        return "neutral", 0.2


class MeanReversionStrategy:
    """RSI extremes + Bollinger bounce."""
    name = "mean_reversion"

    def evaluate(self, candles: List[Candle]) -> Tuple[str, float]:
        if len(candles) < 30:
            return "neutral", 0.0
        closes = [c.close for c in candles]
        rsi_arr = rsi(closes)
        _, _, bb_lower = bollinger_bands(closes)
        last_close = closes[-1]
        last_rsi = rsi_arr[-1]
        if last_rsi < 30 and last_close <= bb_lower[-1]:
            return "bullish", min(1.0, 0.5 + (30 - last_rsi) * 0.015)
        if last_rsi > 70 and last_close >= bb_lower[-1]:
            _, _, bb_upper = bollinger_bands(closes)
            if last_close >= bb_upper[-1]:
                return "bearish", min(1.0, 0.5 + (last_rsi - 70) * 0.015)
        return "neutral", 0.2


class BreakoutStrategy:
    """S/R breakout with volume confirmation."""
    name = "breakout"

    def evaluate(self, candles: List[Candle]) -> Tuple[str, float]:
        if len(candles) < 30:
            return "neutral", 0.0
        pa = price_action_snapshot(candles)
        breakout = pa.get("breakout", "none")
        vol_state = pa.get("volume_state", "low")
        conf = 0.5 if vol_state == "high" else 0.3
        if breakout == "bullish_breakout":
            return "bullish", conf
        if breakout == "bearish_breakout":
            return "bearish", conf
        return "neutral", 0.1


class MomentumStrategy:
    """MACD + Stochastic alignment."""
    name = "momentum"

    def evaluate(self, candles: List[Candle]) -> Tuple[str, float]:
        if len(candles) < 30:
            return "neutral", 0.0
        closes = [c.close for c in candles]
        _, _, hist = macd(closes)
        k, d = stochastic([c.high for c in candles], [c.low for c in candles], closes)
        if hist[-1] > 0 and k[-1] > d[-1] and k[-1] < 80:
            return "bullish", 0.5
        if hist[-1] < 0 and k[-1] < d[-1] and k[-1] > 20:
            return "bearish", 0.5
        return "neutral", 0.2


class PriceActionPatternsStrategy:
    """Candlestick patterns + S/R context."""
    name = "price_action_patterns"

    def evaluate(self, candles: List[Candle]) -> Tuple[str, float]:
        if len(candles) < 5:
            return "neutral", 0.0
        patterns = detect_patterns(candles)
        if not patterns:
            return "neutral", 0.1
        return net_pattern_signal(patterns)


class IchimokuCloudStrategy:
    """Tenkan/Kijun cross + cloud color."""
    name = "ichimoku_cloud"

    def evaluate(self, candles: List[Candle]) -> Tuple[str, float]:
        if len(candles) < 60:
            return "neutral", 0.0
        tenkan, kijun, senkou_a, senkou_b = ichimoku(
            [c.high for c in candles], [c.low for c in candles], [c.close for c in candles]
        )
        last_close = candles[-1].close
        t, k, sa, sb = tenkan[-1], kijun[-1], senkou_a[-1], senkou_b[-1]
        if t > k and last_close > sa and last_close > sb:
            return "bullish", 0.55
        if t < k and last_close < sa and last_close < sb:
            return "bearish", 0.55
        return "neutral", 0.2


class ParabolicSARStrategy:
    """PSAR flip as trailing stop signal."""
    name = "psar_trail"

    def evaluate(self, candles: List[Candle]) -> Tuple[str, float]:
        if len(candles) < 5:
            return "neutral", 0.0
        psar = parabolic_sar(
            [c.high for c in candles], [c.low for c in candles], [c.close for c in candles]
        )
        last_close = candles[-1].close
        if last_close > psar[-1] and psar[-1] < psar[-2]:
            return "bullish", 0.4  # just flipped up
        if last_close < psar[-1] and psar[-1] > psar[-2]:
            return "bearish", 0.4  # just flipped down
        if last_close > psar[-1]:
            return "bullish", 0.3
        if last_close < psar[-1]:
            return "bearish", 0.3
        return "neutral", 0.1


class VWAPMeanStrategy:
    """VWAP deviation + RSI confirmation."""
    name = "vwap_mean"

    def evaluate(self, candles: List[Candle]) -> Tuple[str, float]:
        if len(candles) < 20:
            return "neutral", 0.0
        vwap_arr = vwap(
            [c.high for c in candles], [c.low for c in candles],
            [c.close for c in candles], [c.volume for c in candles]
        )
        rsi_arr = rsi([c.close for c in candles])
        last_close = candles[-1].close
        vwap_val = vwap_arr[-1]
        deviation = (last_close - vwap_val) / max(vwap_val, 1e-9)
        if deviation < -0.001 and rsi_arr[-1] < 40:
            return "bullish", 0.4  # oversold below VWAP
        if deviation > 0.001 and rsi_arr[-1] > 60:
            return "bearish", 0.4  # overbought above VWAP
        return "neutral", 0.2


ALL_STRATEGIES = [
    TrendFollowingStrategy(),
    MeanReversionStrategy(),
    BreakoutStrategy(),
    MomentumStrategy(),
    PriceActionPatternsStrategy(),
    IchimokuCloudStrategy(),
    ParabolicSARStrategy(),
    VWAPMeanStrategy(),
]


def evaluate_all_strategies(candles: List[Candle]) -> Dict[str, Tuple[str, float]]:
    """Run all strategies and return name -> (direction, confidence)."""
    return {s.name: s.evaluate(candles) for s in ALL_STRATEGIES}
