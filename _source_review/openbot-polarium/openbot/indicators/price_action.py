"""Price action analysis — trend, S/R, breakout, volume, pivots."""

from __future__ import annotations

from typing import List, Tuple

import numpy as np

from ..data.models import Candle


def detect_trend(candles: List[Candle], lookback: int = 20) -> Tuple[str, float]:
    if len(candles) < lookback:
        return "sideways", 0.0
    recent = candles[-lookback:]
    closes = np.array([c.close for c in recent], dtype=float)
    x = np.arange(len(closes), dtype=float)
    slope = np.polyfit(x, closes, 1)[0]
    slope_norm = slope / closes.mean()
    highs = np.array([c.high for c in recent])
    lows = np.array([c.low for c in recent])
    pivot_idx = _find_pivots(highs, lows)
    bull = bear = 0
    for i in range(1, len(pivot_idx)):
        ph = highs[pivot_idx[i-1]]; ch = highs[pivot_idx[i]]
        pl = lows[pivot_idx[i-1]]; cl = lows[pivot_idx[i]]
        if ch > ph and cl > pl: bull += 1
        elif ch < ph and cl < pl: bear += 1
    if slope_norm > 0.0005 and bull >= bear:
        return "bullish", min(1.0, abs(slope_norm)*1000 + bull/max(1,len(pivot_idx)))
    if slope_norm < -0.0005 and bear >= bull:
        return "bearish", min(1.0, abs(slope_norm)*1000 + bear/max(1,len(pivot_idx)))
    return "sideways", 0.3


def _find_pivots(highs, lows, window=3):
    pivots = []
    for i in range(window, len(highs) - window):
        is_pivot = True
        for j in range(1, window + 1):
            if highs[i] <= highs[i-j] or highs[i] <= highs[i+j]:
                is_pivot = False
                break
        if not is_pivot:
            for j in range(1, window + 1):
                if lows[i] >= lows[i-j] or lows[i] >= lows[i+j]:
                    is_pivot = False
                    break
            else:
                is_pivot = True
        if is_pivot:
            pivots.append(i)
    return pivots if pivots else list(range(0, len(highs), max(1, len(highs)//4)))


def find_support_resistance(candles, lookback=50, cluster_pct=0.001):
    if len(candles) < 5:
        return {"resistance": None, "support": None, "all_resistances": [], "all_supports": []}
    recent = candles[-lookback:] if len(candles) >= lookback else candles
    res_levels, sup_levels = [], []
    for i in range(2, len(recent) - 2):
        c = recent[i]
        if (c.high > recent[i-1].high and c.high > recent[i-2].high
                and c.high > recent[i+1].high and c.high > recent[i+2].high):
            res_levels.append(c.high)
        if (c.low < recent[i-1].low and c.low < recent[i-2].low
                and c.low < recent[i+1].low and c.low > recent[i+2].low):
            sup_levels.append(c.low)
    res_clusters = _cluster_levels(res_levels, cluster_pct)
    sup_clusters = _cluster_levels(sup_levels, cluster_pct)
    last_price = candles[-1].close
    nearest_res = min((np.mean(c) for c in res_clusters if np.mean(c) > last_price), default=None)
    nearest_sup = max((np.mean(c) for c in sup_clusters if np.mean(c) < last_price), default=None)
    return {
        "resistance": round(float(nearest_res), 6) if nearest_res else None,
        "support": round(float(nearest_sup), 6) if nearest_sup else None,
        "all_resistances": [round(float(np.mean(c)), 6) for c in res_clusters],
        "all_supports": [round(float(np.mean(c)), 6) for c in sup_clusters],
    }


def _cluster_levels(levels, cluster_pct):
    if not levels: return []
    s = sorted(levels)
    clusters = [[s[0]]]
    for lvl in s[1:]:
        last = clusters[-1][-1]
        if abs(lvl - last) / last < cluster_pct:
            clusters[-1].append(lvl)
        else:
            clusters.append([lvl])
    return clusters


def detect_breakout(candles, sr, lookback=3):
    if not sr.get("resistance") or not sr.get("support"):
        return "none"
    last_close = candles[-1].close
    avg_recent = sum(c.close for c in candles[-lookback:]) / lookback
    if last_close > sr["resistance"] and avg_recent < sr["resistance"]:
        return "bullish_breakout"
    if last_close < sr["support"] and avg_recent > sr["support"]:
        return "bearish_breakout"
    return "none"


def analyze_volume(candles, lookback=20):
    if len(candles) < lookback:
        return {"state": "low", "ratio": 1.0}
    recent_vols = [c.volume for c in candles[-3:]]
    avg_vol = np.mean([c.volume for c in candles[-lookback:]])
    if avg_vol == 0:
        return {"state": "low", "ratio": 1.0}
    cur_ratio = float(np.mean(recent_vols) / avg_vol)
    state = "high" if cur_ratio > 1.5 else "low" if cur_ratio < 0.7 else "normal"
    return {"state": state, "ratio": round(cur_ratio, 2)}


def price_action_snapshot(candles):
    if len(candles) < 10:
        return {"_error": "not enough candles"}
    trend, trend_conf = detect_trend(candles, lookback=20)
    sr = find_support_resistance(candles, lookback=50)
    breakout = detect_breakout(candles, sr)
    volume = analyze_volume(candles)
    last = candles[-1]
    return {
        "trend": trend, "trend_confidence": trend_conf,
        "support": sr["support"], "resistance": sr["resistance"],
        "breakout": breakout,
        "volume_state": volume["state"], "volume_ratio": volume["ratio"],
        "last_close": last.close,
        "last_bull": last.is_bull,
        "last_body": round(last.body, 6),
        "last_range": round(last.range_, 6),
        "all_resistances": sr["all_resistances"],
        "all_supports": sr["all_supports"],
    }
