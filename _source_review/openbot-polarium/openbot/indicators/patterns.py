"""Candlestick patterns detector — v1 kept + v2 additions."""

from __future__ import annotations

from typing import List, Tuple

from ..data.models import Candle


def _body(c): return abs(c.close - c.open)
def _uw(c): return c.high - max(c.open, c.close)
def _lw(c): return min(c.open, c.close) - c.low
def _rng(c): return max(c.high - c.low, 1e-12)
def _bull(c): return c.close > c.open
def _bear(c): return c.close < c.open


def detect_doji(candles):
    out = []
    for c in candles[-3:]:
        if _body(c) <= 0.05 * _rng(c):
            out.append(("Doji", "neutral", 0.5))
    return out

def detect_hammer(candles):
    out = []
    if len(candles) < 1: return out
    c = candles[-1]
    body = _body(c)
    if body == 0: return out
    if _lw(c) >= 2 * body and _uw(c) <= 0.3 * body:
        prior_bear = len(candles) >= 3 and candles[-3].close > candles[-1].close
        out.append(("Hammer" if prior_bear else "Hanging Man",
                    "bullish" if prior_bear else "bearish", 0.65))
    return out

def detect_shooting_star(candles):
    out = []
    if len(candles) < 1: return out
    c = candles[-1]
    body = _body(c)
    if body == 0: return out
    if _uw(c) >= 2 * body and _lw(c) <= 0.3 * body:
        if len(candles) >= 3 and candles[-3].close < candles[-1].close:
            out.append(("Shooting Star", "bearish", 0.65))
    return out

def detect_engulfing(candles):
    out = []
    if len(candles) < 2: return out
    prev, cur = candles[-2], candles[-1]
    if _bear(prev) and _bull(cur) and cur.close >= prev.open and cur.open <= prev.close:
        s = min(1.0, _body(cur) / max(_body(prev), 1e-12) / 2)
        out.append(("Bullish Engulfing", "bullish", round(0.5 + 0.4*s, 2)))
    elif _bull(prev) and _bear(cur) and cur.open >= prev.close and cur.close <= prev.open:
        s = min(1.0, _body(cur) / max(_body(prev), 1e-12) / 2)
        out.append(("Bearish Engulfing", "bearish", round(0.5 + 0.4*s, 2)))
    return out

def detect_harami(candles):
    out = []
    if len(candles) < 2: return out
    prev, cur = candles[-2], candles[-1]
    if _body(prev) > 2 * _body(cur) and prev.low < cur.low and prev.high > cur.high:
        if _bear(prev) and _bull(cur):
            out.append(("Bullish Harami", "bullish", 0.55))
        elif _bull(prev) and _bear(cur):
            out.append(("Bearish Harami", "bearish", 0.55))
    return out

def detect_piercing_line(candles):
    out = []
    if len(candles) < 2: return out
    prev, cur = candles[-2], candles[-1]
    if _bear(prev) and _bull(cur):
        mid = (prev.open + prev.close) / 2
        if cur.open < prev.low and cur.close > mid and cur.close < prev.open:
            out.append(("Piercing Line", "bullish", 0.6))
    return out

def detect_dark_cloud_cover(candles):
    out = []
    if len(candles) < 2: return out
    prev, cur = candles[-2], candles[-1]
    if _bull(prev) and _bear(cur):
        mid = (prev.open + prev.close) / 2
        if cur.open > prev.high and cur.close < mid and cur.close > prev.open:
            out.append(("Dark Cloud Cover", "bearish", 0.6))
    return out

def detect_morning_evening_star(candles):
    out = []
    if len(candles) < 3: return out
    c1, c2, c3 = candles[-3], candles[-2], candles[-1]
    if (_bear(c1) and _body(c2) < 0.3 * _body(c1) and _bull(c3)
            and c3.close > (c1.open + c1.close) / 2):
        out.append(("Morning Star", "bullish", 0.75))
    if (_bull(c1) and _body(c2) < 0.3 * _body(c1) and _bear(c3)
            and c3.close < (c1.open + c1.close) / 2):
        out.append(("Evening Star", "bearish", 0.75))
    return out

def detect_three_soldiers(candles):
    out = []
    if len(candles) < 3: return out
    c1, c2, c3 = candles[-3], candles[-2], candles[-1]
    if all(_bull(c) for c in (c1, c2, c3)):
        if c2.close > c1.close and c3.close > c2.close and c2.open > c1.open and c3.open > c2.open:
            out.append(("Three White Soldiers", "bullish", 0.8))
    if all(_bear(c) for c in (c1, c2, c3)):
        if c2.close < c1.close and c3.close < c2.close and c2.open < c1.open and c3.open < c2.open:
            out.append(("Three Black Crows", "bearish", 0.8))
    return out

def detect_tweezer(candles):
    out = []
    if len(candles) < 2: return out
    prev, cur = candles[-2], candles[-1]
    tol = 0.0002 * cur.close
    if _bear(prev) and _bull(cur) and abs(prev.low - cur.low) < tol:
        out.append(("Tweezer Bottom", "bullish", 0.55))
    if _bull(prev) and _bear(cur) and abs(prev.high - cur.high) < tol:
        out.append(("Tweezer Top", "bearish", 0.55))
    return out

# NEW v2 patterns
def detect_marubozu(candles):
    out = []
    if len(candles) < 1: return out
    c = candles[-1]
    body = _body(c)
    if body == 0: return out
    if _uw(c) <= 0.05 * body and _lw(c) <= 0.05 * body:
        out.append(("Marubozu Bull" if _bull(c) else "Marubozu Bear",
                    "bullish" if _bull(c) else "bearish", 0.6))
    return out

def detect_spinning_top(candles):
    out = []
    if len(candles) < 1: return out
    c = candles[-1]
    body = _body(c)
    if body == 0: return out
    if body <= 0.3 * _rng(c) and _uw(c) > 0.3 * _rng(c) and _lw(c) > 0.3 * _rng(c):
        out.append(("Spinning Top", "neutral", 0.4))
    return out

def detect_abandoned_baby(candles):
    out = []
    if len(candles) < 3: return out
    c1, c2, c3 = candles[-3], candles[-2], candles[-1]
    if _bear(c1) and _body(c2) < 0.1 * _body(c1) and c2.high < c1.low and _bull(c3) and c3.low > c2.high:
        out.append(("Abandoned Baby Bull", "bullish", 0.8))
    if _bull(c1) and _body(c2) < 0.1 * _body(c1) and c2.low > c1.high and _bear(c3) and c3.high < c2.low:
        out.append(("Abandoned Baby Bear", "bearish", 0.8))
    return out

_DETECTORS = [
    detect_doji, detect_hammer, detect_shooting_star,
    detect_engulfing, detect_harami, detect_piercing_line,
    detect_dark_cloud_cover, detect_morning_evening_star,
    detect_three_soldiers, detect_tweezer,
    detect_marubozu, detect_spinning_top, detect_abandoned_baby,
]

def detect_patterns(candles):
    if len(candles) < 3: return []
    results = []
    for fn in _DETECTORS:
        for name, dir_, conf in fn(candles):
            results.append({"name": name, "direction": dir_, "confidence": conf})
    by_name = {}
    for r in results:
        if r["name"] not in by_name or r["confidence"] > by_name[r["name"]]["confidence"]:
            by_name[r["name"]] = r
    return list(by_name.values())

def net_pattern_signal(patterns):
    if not patterns:
        return "neutral", 0.0
    bull = sum(p["confidence"] for p in patterns if p["direction"] == "bullish")
    bear = sum(p["confidence"] for p in patterns if p["direction"] == "bearish")
    if bull > bear:
        return "bullish", min(1.0, bull / (bull + bear + 0.001))
    if bear > bull:
        return "bearish", min(1.0, bear / (bull + bear + 0.001))
    return "neutral", 0.5
