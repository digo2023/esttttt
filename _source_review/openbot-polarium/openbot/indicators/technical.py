"""
Extended technical indicators — v2.0

Adds: ADX, Ichimoku, Parabolic SAR, OBV, VWAP, Williams %R, CCI,
Pivot Points, Fibonacci, ATR Bands — on top of the v1 set
(RSI, MACD, Bollinger, EMA, SMA, Stochastic, ATR).
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

import numpy as np
import pandas as pd

from ..data.models import Candle


def to_dataframe(candles: Sequence[Candle]) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(
        {
            "open": [c.open for c in candles],
            "high": [c.high for c in candles],
            "low": [c.low for c in candles],
            "close": [c.close for c in candles],
            "volume": [c.volume for c in candles],
        },
        index=pd.to_datetime([c.timestamp for c in candles], unit="s"),
    )
    return df


# ==================== v1 indicators (kept) ====================

def sma(s, p): return pd.Series(s).rolling(p, min_periods=p).mean().to_numpy()
def ema(s, p): return pd.Series(s).ewm(span=p, adjust=False).mean().to_numpy()

def rsi(closes, period=14):
    s = pd.Series(closes).astype(float)
    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50).to_numpy()

def macd(closes, fast=12, slow=26, signal=9):
    s = pd.Series(closes).astype(float)
    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line.to_numpy(), signal_line.to_numpy(), hist.to_numpy()

def bollinger_bands(closes, period=20, std_mult=2.0):
    s = pd.Series(closes).astype(float)
    mid = s.rolling(period, min_periods=period).mean()
    sd = s.rolling(period, min_periods=period).std()
    return (mid + std_mult*sd).to_numpy(), mid.to_numpy(), (mid - std_mult*sd).to_numpy()

def stochastic(highs, lows, closes, k=14, d=3):
    h = pd.Series(highs).astype(float)
    l = pd.Series(lows).astype(float)
    c = pd.Series(closes).astype(float)
    ll = l.rolling(k, min_periods=k).min()
    hh = h.rolling(k, min_periods=k).max()
    k_line = (100 * (c - ll) / (hh - ll).replace(0, np.nan)).fillna(50)
    d_line = k_line.rolling(d, min_periods=d).mean().fillna(50)
    return k_line.to_numpy(), d_line.to_numpy()

def atr(candles, period=14):
    h = np.array([c.high for c in candles], dtype=float)
    l = np.array([c.low for c in candles], dtype=float)
    c_prev = np.array([candles[i-1].close if i > 0 else candles[0].close for i in range(len(candles))], dtype=float)
    tr = np.maximum.reduce([h - l, np.abs(h - c_prev), np.abs(l - c_prev)])
    return pd.Series(tr).ewm(alpha=1/period, adjust=False).mean().to_numpy()


# ==================== NEW v2 indicators ====================

def adx(highs, lows, closes, period=14):
    """Average Directional Index — trend strength (regardless of direction)."""
    h = pd.Series(highs).astype(float)
    l = pd.Series(lows).astype(float)
    c = pd.Series(closes).astype(float)
    up = h.diff()
    down = -l.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = np.maximum.reduce([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()])
    atr_ = pd.Series(tr).ewm(alpha=1/period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm).ewm(alpha=1/period, adjust=False).mean() / atr_.replace(0, np.nan)
    minus_di = 100 * pd.Series(minus_dm).ewm(alpha=1/period, adjust=False).mean() / atr_.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_val = dx.ewm(alpha=1/period, adjust=False).mean().fillna(0)
    return adx_val.to_numpy(), plus_di.fillna(0).to_numpy(), minus_di.fillna(0).to_numpy()


def ichimoku(highs, lows, closes, tenkan=9, kijun=26, senkou=52):
    """Ichimoku Cloud. Returns (tenkan, kijun, senkou_a, senkou_b)."""
    h = pd.Series(highs).astype(float)
    l = pd.Series(lows).astype(float)
    c = pd.Series(closes).astype(float)
    tenkan_val = (h.rolling(tenkan).max() + l.rolling(tenkan).min()) / 2
    kijun_val = (h.rolling(kijun).max() + l.rolling(kijun).min()) / 2
    senkou_a = ((tenkan_val + kijun_val) / 2).shift(kijun)
    senkou_b = ((h.rolling(senkou).max() + l.rolling(senkou).min()) / 2).shift(kijun)
    return (tenkan_val.fillna(0).to_numpy(),
            kijun_val.fillna(0).to_numpy(),
            senkou_a.fillna(0).to_numpy(),
            senkou_b.fillna(0).to_numpy())


def parabolic_sar(highs, lows, closes, step=0.02, max_step=0.2):
    """Parabolic SAR. Returns numpy array of SAR values."""
    h = np.array(highs, dtype=float)
    l = np.array(lows, dtype=float)
    c = np.array(closes, dtype=float)
    n = len(h)
    if n < 2:
        return np.zeros(n)
    sar = np.zeros(n)
    sar[0] = h[0]
    af = step
    ep = h[0]
    trend = 1  # 1=up, -1=down
    for i in range(1, n):
        if trend == 1:
            sar[i] = sar[i-1] + af * (ep - sar[i-1])
            if l[i] < sar[i]:
                trend = -1
                sar[i] = ep
                ep = l[i]
                af = step
            else:
                if h[i] > ep:
                    ep = h[i]
                    af = min(af + step, max_step)
        else:
            sar[i] = sar[i-1] + af * (ep - sar[i-1])
            if h[i] > sar[i]:
                trend = 1
                sar[i] = ep
                ep = h[i]
                af = step
            else:
                if l[i] < ep:
                    ep = l[i]
                    af = min(af + step, max_step)
    return sar


def obv(closes, volumes):
    """On-Balance Volume."""
    c = pd.Series(closes).astype(float)
    v = pd.Series(volumes).astype(float)
    sign = np.sign(c.diff().fillna(0))
    return (sign * v).cumsum().to_numpy()


def vwap(highs, lows, closes, volumes):
    """Volume-Weighted Average Price (rolling)."""
    h = np.array(highs, dtype=float)
    l = np.array(lows, dtype=float)
    c = np.array(closes, dtype=float)
    v = np.array(volumes, dtype=float)
    typical = (h + l + c) / 3
    cum_pv = np.cumsum(typical * v)
    cum_v = np.cumsum(v)
    return cum_pv / np.maximum(cum_v, 1e-12)


def williams_r(highs, lows, closes, period=14):
    """Williams %R (-100 to 0)."""
    h = pd.Series(highs).astype(float)
    l = pd.Series(lows).astype(float)
    c = pd.Series(closes).astype(float)
    hh = h.rolling(period, min_periods=period).max()
    ll = l.rolling(period, min_periods=period).min()
    wr = -100 * (hh - c) / (hh - ll).replace(0, np.nan)
    return wr.fillna(-50).to_numpy()


def cci(highs, lows, closes, period=20):
    """Commodity Channel Index."""
    h = np.array(highs, dtype=float)
    l = np.array(lows, dtype=float)
    c = np.array(closes, dtype=float)
    typical = pd.Series((h + l + c) / 3)
    sma_t = typical.rolling(period, min_periods=period).mean()
    mean_dev = typical.rolling(period, min_periods=period).apply(
        lambda x: np.mean(np.abs(x - x.mean())), raw=True
    )
    cci_val = (typical - sma_t) / (0.015 * mean_dev.replace(0, np.nan))
    return cci_val.fillna(0).to_numpy()


def pivot_points(highs, lows, closes, method="standard"):
    """Pivot Points and supports/resistances for the latest period."""
    h = highs[-1]
    l = lows[-1]
    c = closes[-1]
    if method == "standard":
        pivot = (h + l + c) / 3
        r1 = 2 * pivot - l
        s1 = 2 * pivot - h
        r2 = pivot + (h - l)
        s2 = pivot - (h - l)
        r3 = h + 2 * (pivot - l)
        s3 = l - 2 * (h - pivot)
        return {"pivot": pivot, "r1": r1, "r2": r2, "r3": r3, "s1": s1, "s2": s2, "s3": s3}
    # Fibonacci method
    pivot = (h + l + c) / 3
    return {
        "pivot": pivot,
        "r1": pivot + 0.382*(h-l), "r2": pivot + 0.618*(h-l), "r3": pivot + 1.0*(h-l),
        "s1": pivot - 0.382*(h-l), "s2": pivot - 0.618*(h-l), "s3": pivot - 1.0*(h-l),
    }


def fibonacci_levels(candles: List[Candle], lookback: int = 50) -> dict:
    """Auto-draw Fibonacci retracement levels from recent swing high/low."""
    recent = candles[-lookback:] if len(candles) >= lookback else candles
    swing_high = max(c.high for c in recent)
    swing_low = min(c.low for c in recent)
    diff = swing_high - swing_low
    return {
        "swing_high": swing_high,
        "swing_low": swing_low,
        "level_0": swing_high,
        "level_236": swing_high - 0.236 * diff,
        "level_382": swing_high - 0.382 * diff,
        "level_500": swing_high - 0.500 * diff,
        "level_618": swing_high - 0.618 * diff,
        "level_786": swing_high - 0.786 * diff,
        "level_100": swing_low,
    }


def atr_bands(candles, period=14, multiplier=2.5):
    """ATR-based bands around the close (similar to Keltner but with ATR)."""
    closes = np.array([c.close for c in candles], dtype=float)
    atr_arr = atr(candles, period)
    sma_arr = sma(closes, period)
    upper = sma_arr + multiplier * atr_arr
    lower = sma_arr - multiplier * atr_arr
    return upper, sma_arr, lower


def compute_indicators(candles: List[Candle], params: dict | None = None) -> dict:
    """Compute ALL indicators for the latest candle."""
    p = params or {}
    if len(candles) < 60:
        return {"_error": "not enough candles", "_count": len(candles)}

    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    vols = [c.volume for c in candles]

    # v1
    rsi_arr = rsi(closes, p.get("rsi_period", 14))
    macd_line, signal_line, hist = macd(closes)
    bb_upper, bb_mid, bb_lower = bollinger_bands(closes)
    ema_fast = ema(closes, p.get("ema_fast", 9))
    ema_slow = ema(closes, p.get("ema_slow", 21))
    sma_fast = sma(closes, p.get("sma_fast", 20))
    sma_slow = sma(closes, p.get("sma_slow", 50))
    stoch_k, stoch_d = stochastic(highs, lows, closes)
    atr_arr = atr(candles)

    # v2 NEW
    adx_val, plus_di, minus_di = adx(highs, lows, closes)
    tenkan, kijun, senkou_a, senkou_b = ichimoku(highs, lows, closes)
    psar_arr = parabolic_sar(highs, lows, closes)
    obv_arr = obv(closes, vols)
    vwap_arr = vwap(highs, lows, closes, vols)
    wr_arr = williams_r(highs, lows, closes)
    cci_arr = cci(highs, lows, closes)
    pp = pivot_points(highs[-2:], lows[-2:], closes[-2:])
    fib = fibonacci_levels(candles)
    atr_b_upper, atr_b_mid, atr_b_lower = atr_bands(candles)

    i = -1
    last_close = closes[i]

    # Direction votes from each indicator
    votes = {}
    votes["rsi"] = ("bullish", 0.4) if rsi_arr[i] <= 30 else ("bearish", 0.4) if rsi_arr[i] >= 70 else ("neutral", 0.1)
    votes["macd"] = ("bullish", 0.4) if hist[i] > 0 else ("bearish", 0.4) if hist[i] < 0 else ("neutral", 0.1)
    votes["ema_cross"] = ("bullish", 0.3) if ema_fast[i] > ema_slow[i] else ("bearish", 0.3)
    votes["sma_cross"] = ("bullish", 0.3) if sma_fast[i] > sma_slow[i] else ("bearish", 0.3)
    votes["bollinger"] = ("bullish", 0.3) if last_close < bb_lower[i] else ("bearish", 0.3) if last_close > bb_upper[i] else ("neutral", 0.1)
    votes["stochastic"] = ("bullish", 0.3) if stoch_k[i] < 20 else ("bearish", 0.3) if stoch_k[i] > 80 else ("neutral", 0.1)
    votes["adx"] = ("bullish" if plus_di[i] > minus_di[i] else "bearish", 0.5 if adx_val[i] > 25 else 0.2)
    votes["ichimoku"] = ("bullish", 0.4) if (tenkan[i] > kijun[i] and last_close > senkou_a[i] and last_close > senkou_b[i]) else \
                        ("bearish", 0.4) if (tenkan[i] < kijun[i] and last_close < senkou_a[i] and last_close < senkou_b[i]) else ("neutral", 0.1)
    votes["psar"] = ("bullish", 0.35) if last_close > psar_arr[i] else ("bearish", 0.35)
    votes["obv"] = ("bullish", 0.25) if obv_arr[i] > obv_arr[i-1] else ("bearish", 0.25)
    votes["vwap"] = ("bullish", 0.25) if last_close > vwap_arr[i] else ("bearish", 0.25)
    votes["williams_r"] = ("bullish", 0.3) if wr_arr[i] < -80 else ("bearish", 0.3) if wr_arr[i] > -20 else ("neutral", 0.1)
    votes["cci"] = ("bullish", 0.3) if cci_arr[i] < -100 else ("bearish", 0.3) if cci_arr[i] > 100 else ("neutral", 0.1)
    # Distance to pivot
    pivot_dist = (last_close - pp["pivot"]) / max(abs(pp["pivot"]), 1e-9)
    votes["pivot"] = ("bearish", 0.25) if pivot_dist > 0.001 else ("bullish", 0.25) if pivot_dist < -0.001 else ("neutral", 0.1)
    # Fibonacci level proximity
    fib_bull = fib["level_618"] < last_close < fib["level_500"]
    fib_bear = fib["level_500"] < last_close < fib["level_618"]
    votes["fibonacci"] = ("bullish", 0.25) if fib_bull else ("bearish", 0.25) if fib_bear else ("neutral", 0.1)
    votes["atr_bands"] = ("bullish", 0.2) if last_close < atr_b_lower[i] else ("bearish", 0.2) if last_close > atr_b_upper[i] else ("neutral", 0.05)

    return {
        "price": last_close,
        "votes": votes,
        "rsi": round(float(rsi_arr[i]), 2),
        "rsi_state": "oversold" if rsi_arr[i] < 30 else "overbought" if rsi_arr[i] > 70 else "neutral",
        "macd_hist": round(float(hist[i]), 6),
        "macd_state": "bullish" if hist[i] > 0 else "bearish",
        "bollinger_state": ("above_upper" if last_close > bb_upper[i] else
                             "below_lower" if last_close < bb_lower[i] else "inside"),
        "ema_state": "bullish" if ema_fast[i] > ema_slow[i] else "bearish",
        "sma_state": "bullish" if sma_fast[i] > sma_slow[i] else "bearish",
        "stoch_k": round(float(stoch_k[i]), 2),
        "stoch_state": "oversold" if stoch_k[i] < 20 else "overbought" if stoch_k[i] > 80 else "neutral",
        "atr": round(float(atr_arr[i]), 6),
        "adx": round(float(adx_val[i]), 2),
        "adx_state": "strong" if adx_val[i] > 25 else "weak",
        "di_state": "bullish" if plus_di[i] > minus_di[i] else "bearish",
        "ichimoku_state": ("bullish" if tenkan[i] > kijun[i] and last_close > senkou_a[i] else
                            "bearish" if tenkan[i] < kijun[i] and last_close < senkou_a[i] else "neutral"),
        "psar_state": "bullish" if last_close > psar_arr[i] else "bearish",
        "obv_trend": "up" if obv_arr[i] > obv_arr[i-1] else "down",
        "vwap_state": "above" if last_close > vwap_arr[i] else "below",
        "williams_r": round(float(wr_arr[i]), 2),
        "cci": round(float(cci_arr[i]), 2),
        "pivot": round(float(pp["pivot"]), 6),
        "pivot_dist_pct": round(pivot_dist * 100, 3),
        "fib_level": ("at_618" if abs(last_close - fib["level_618"]) / last_close < 0.001 else
                      "at_500" if abs(last_close - fib["level_500"]) / last_close < 0.001 else
                      "at_382" if abs(last_close - fib["level_382"]) / last_close < 0.001 else "between"),
        "atr_bands_state": ("below_lower" if last_close < atr_b_lower[i] else
                            "above_upper" if last_close > atr_b_upper[i] else "inside"),
    }
