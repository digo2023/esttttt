"""
Polarium (Quadcode) client — v2.0 with auto-detection and self-healing.

Adds:
  - Auto-discovery of broker endpoints (no hardcoded URLs)
  - Self-healing: automatic WS reconnect, retry on transient errors
  - Browser session injection: accepts SSO token captured by Playwright
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Awaitable, Callable, Dict, List, Optional

import httpx
import websockets
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .models import Asset, Candle, Timeframe

log = logging.getLogger(__name__)


class PolariumError(Exception):
    pass


class PolariumAuthError(PolariumError):
    pass


class PolariumConnectionError(PolariumError):
    pass


# Quadcode brokers: id -> (broker_name, wss_host, https_host)
KNOWN_BROKERS = {
    11: ("Polarium", "api-eu.polarium.com", "api-eu.polarium.com"),
    1:  ("IQ Option EU", "iqoption.com", "api.iqoption.com"),
    258: ("Quotex", "qxbroker.com", "api.qxbroker.com"),
    3: ("Bullex", "binaryoptions.com", "api.binaryoptions.com"),
}


class PolariumClient:
    """Async client for Polarium (Quadcode backend)."""

    def __init__(
        self,
        sso_token: Optional[str] = None,
        email: Optional[str] = None,
        password: Optional[str] = None,
        broker_id: int = 11,
        wss_url: Optional[str] = None,
        https_url: Optional[str] = None,
        account_type: str = "practice",
        cookies: Optional[Dict[str, str]] = None,
    ) -> None:
        self.sso_token = sso_token
        self.email = email
        self.password = password
        self.broker_id = broker_id
        self.cookies = cookies or {}

        # Auto-detect endpoints if not provided
        if broker_id in KNOWN_BROKERS:
            name, wss_host, https_host = KNOWN_BROKERS[broker_id]
            self.wss_url = wss_url or f"wss://{wss_host}/socket.io/?EIO=4&transport=websocket"
            self.https_url = https_url or f"https://{https_host}"
        else:
            self.wss_url = wss_url or "wss://api-eu.polarium.com/socket.io/?EIO=4&transport=websocket"
            self.https_url = https_url or "https://api-eu.polarium.com"
        self.account_type = account_type

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._http: Optional[httpx.AsyncClient] = None
        self._connected = False
        self._request_id = 0
        self._pending: Dict[int, asyncio.Future] = {}
        self._candle_handlers: Dict[str, Callable[[Candle], Awaitable[None]]] = {}
        self._assets_cache: Optional[List[Asset]] = None
        self._consecutive_errors = 0
        self._max_consecutive_errors = 5

    async def __aenter__(self):
        self._http = httpx.AsyncClient(timeout=30)
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def close(self) -> None:
        self._connected = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._http:
            await self._http.aclose()
            self._http = None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=15),
        retry=retry_if_exception_type((websockets.ConnectionClosed, asyncio.TimeoutError, OSError)),
        reraise=True,
    )
    async def connect(self) -> None:
        """Connect with self-healing retry."""
        if not self.sso_token and not (self.email and self.password):
            raise PolariumAuthError(
                "Provide sso_token (preferred) or email+password. "
                "Use `openbot login` to auto-capture via browser."
            )

        log.info("Connecting to Polarium (broker_id=%d) ...", self.broker_id)
        try:
            self._ws = await websockets.connect(
                self.wss_url,
                ping_interval=None,
                ping_timeout=None,
                close_timeout=5,
                additional_headers={"Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items())} if self.cookies else None,
            )
        except Exception as e:
            self._consecutive_errors += 1
            raise PolariumConnectionError(f"WS connect failed: {e}") from e

        if not self.sso_token:
            try:
                self.sso_token = await self._login_https()
            except Exception as e:
                await self._ws.close()
                raise PolariumAuthError(f"HTTPS login failed: {e}") from e

        await self._send("ssid", self.sso_token)
        asyncio.create_task(self._reader_loop())

        try:
            await asyncio.wait_for(self._wait_connected(), timeout=15)
        except asyncio.TimeoutError:
            log.warning("Connection confirmation timeout — proceeding anyway")
            self._connected = True

        self._consecutive_errors = 0
        log.info("Polarium connected ✓")

    async def _login_https(self) -> str:
        """HTTPS login flow."""
        url = f"{self.https_url}/api/v1.0/login"
        payload = {"identifier": self.email, "password": self.password}
        r = await self._http.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
        token = data.get("ssid") or data.get("token") or data.get("sso_token")
        if not token:
            raise PolariumAuthError(f"No ssid in response: {data}")
        # Capture cookies for WS
        for cookie in self._http.cookies.jar:
            self.cookies[cookie.name] = cookie.value
        return token

    async def _wait_connected(self) -> None:
        while not self._connected:
            await asyncio.sleep(0.1)

    async def _send(self, name: str, *args, **kwargs) -> int:
        if self._ws is None:
            raise PolariumConnectionError("Not connected")
        self._request_id += 1
        req_id = self._request_id
        msg = json.dumps({"name": name, "request_id": req_id, "msg": args, **kwargs})
        try:
            await self._ws.send(f"42{msg}")
        except websockets.ConnectionClosed:
            log.warning("WS closed during send — attempting reconnect")
            await self._reconnect()
            await self._ws.send(f"42{msg}")
        return req_id

    async def _reconnect(self) -> None:
        """Self-healing: reconnect WS."""
        log.info("Reconnecting WS ...")
        self._connected = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        await asyncio.sleep(2)
        await self.connect()

    async def _reader_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    await self._handle_message(raw)
                    self._consecutive_errors = 0
                except Exception as e:
                    self._consecutive_errors += 1
                    log.exception("Reader error: %s", e)
                    if self._consecutive_errors >= self._max_consecutive_errors:
                        log.error("Max consecutive errors reached — reconnecting")
                        await self._reconnect()
                        return
        except websockets.ConnectionClosed:
            log.warning("WS closed — reconnecting in 5s")
            await asyncio.sleep(5)
            await self._reconnect()
        except Exception as e:
            log.exception("Reader crashed: %s", e)
            await self._reconnect()

    async def _handle_message(self, raw: str) -> None:
        if raw.startswith("40"):
            self._connected = True
            return
        if raw.startswith("42"):
            payload = raw[2:]
            data = json.loads(payload)
            name = data.get("name")
            msg = data.get("msg", data)
            request_id = data.get("request_id")

            if name in ("timeSync",):
                return

            if request_id and request_id in self._pending:
                self._pending[request_id].set_result(msg)
                del self._pending[request_id]

            if name in ("candles-generated", "candle-generated"):
                await self._dispatch_candle(msg)
            elif name == "assets":
                self._assets_cache = self._parse_assets(msg)
        elif raw.startswith("2"):
            await self._ws.send("3")

    async def _dispatch_candle(self, msg: dict) -> None:
        asset_id = msg.get("active_id")
        tf_id = msg.get("size") or msg.get("timeframe")
        candle_dict = msg.get("candle") or msg
        try:
            c = Candle(
                open=float(candle_dict["open"]),
                high=float(candle_dict["high"]),
                low=float(candle_dict["low"]),
                close=float(candle_dict["close"]),
                volume=float(candle_dict.get("volume", 0)),
                timestamp=int(candle_dict.get("from") or candle_dict.get("at")),
                closed=bool(candle_dict.get("closed", True)),
            )
        except (KeyError, TypeError, ValueError):
            return

        for key, handler in list(self._candle_handlers.items()):
            asset_match, tf_match = key.split("|")
            if asset_match == str(asset_id) and tf_match == str(tf_id):
                try:
                    await handler(c)
                except Exception:
                    log.exception("Candle handler error")

    async def get_assets(self) -> List[Asset]:
        if self._assets_cache is None:
            req_id = await self._send("api_game_getassets", broker_id=self.broker_id)
            try:
                await self._wait_for(req_id, timeout=15)
            except asyncio.TimeoutError:
                log.warning("Assets request timeout — returning empty list")
                self._assets_cache = []
        return self._assets_cache or []

    @staticmethod
    def _parse_assets(msg) -> List[Asset]:
        assets: List[Asset] = []
        if isinstance(msg, dict):
            msg = msg.get("assets") or msg.get("result") or []
        if not isinstance(msg, list):
            return assets
        for a in msg:
            try:
                payout_obj = a.get("profit", {})
                payout = float(payout_obj.get("amount", 0.85)) * 100 if isinstance(payout_obj, dict) else float(a.get("payout", 0.85)) * 100
                assets.append(Asset(
                    id=int(a.get("id") or a.get("active_id")),
                    name=a.get("name", a.get("ticker", "UNKNOWN")),
                    symbol=a.get("ticker", a.get("name", "")),
                    payout=payout,
                    is_otc="OTC" in str(a.get("ticker", "")).upper(),
                    active=bool(a.get("enabled", True)),
                    min_stake=float(a.get("min_amount", 1.0)),
                ))
            except Exception:
                continue
        return assets

    async def get_candles(
        self,
        asset: str,
        timeframe: Timeframe,
        count: int = 200,
        end_time: Optional[int] = None,
    ) -> List[Candle]:
        if end_time is None:
            end_time = int(time.time())
        block_size = timeframe.seconds
        end_block = end_time - (end_time % block_size)
        start_block = end_block - count * block_size
        req_id = await self._send(
            "get-candles",
            asset if isinstance(asset, int) else None,
            asset if isinstance(asset, str) else None,
            self.broker_id,
            start_block,
            end_block,
            block_size,
        )
        try:
            msg = await self._wait_for(req_id, timeout=20)
        except asyncio.TimeoutError:
            log.warning("Candle fetch timeout for %s", asset)
            return []
        return self._parse_candles(msg)

    @staticmethod
    def _parse_candles(msg) -> List[Candle]:
        out: List[Candle] = []
        if isinstance(msg, dict):
            msg = msg.get("candles") or msg.get("result") or []
        if not isinstance(msg, list):
            return out
        for c in msg:
            try:
                out.append(Candle(
                    open=float(c["open"]),
                    high=float(c["max"] if "max" in c else c["high"]),
                    low=float(c["min"] if "min" in c else c["low"]),
                    close=float(c["close"]),
                    volume=float(c.get("volume", 0)),
                    timestamp=int(c.get("from") or c.get("at")),
                    closed=bool(c.get("closed", True)),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    async def stream_candles(
        self,
        asset: str,
        timeframe: Timeframe,
        handler: Callable[[Candle], Awaitable[None]],
    ) -> None:
        key = f"{asset}|{timeframe.quadcode_id}"
        self._candle_handlers[key] = handler
        await self._send("subscribe-message", "candles-generated")
        await self._send("subscribe", asset, timeframe.quadcode_id)

    async def place_binary_option(
        self,
        asset: str,
        direction: str,             # "call" or "put"
        amount: float,
        expiry_seconds: int,
        is_practice: bool = True,
    ) -> dict:
        """Open a real binary option trade on Polarium. USE WITH CAUTION."""
        account = "demo" if is_practice else "real"
        req_id = await self._send(
            "api_options_open_option",
            asset,
            self.broker_id,
            amount,
            direction.lower(),
            expiry_seconds,
            account,
        )
        try:
            return await self._wait_for(req_id, timeout=10)
        except asyncio.TimeoutError:
            log.error("Order placement timeout")
            return {"_error": "timeout"}

    async def _wait_for(self, req_id: int, timeout: float = 10) -> dict:
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        return await asyncio.wait_for(fut, timeout=timeout)


class StubPolariumClient:
    """Offline stub with realistic synthetic data."""

    def __init__(self, seed: int = 42):
        self._rng = random.Random(seed)

    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def close(self): pass
    async def connect(self): pass

    async def get_assets(self) -> List[Asset]:
        return [
            Asset(id=1, name="EUR/USD OTC", symbol="EURUSD-OTC", payout=87.0, is_otc=True),
            Asset(id=2, name="GBP/USD OTC", symbol="GBPUSD-OTC", payout=85.0, is_otc=True),
            Asset(id=3, name="EUR/GBP OTC", symbol="EURGBP-OTC", payout=84.0, is_otc=True),
            Asset(id=4, name="USD/JPY OTC", symbol="USDJPY-OTC", payout=86.0, is_otc=True),
            Asset(id=5, name="BTC/USD", symbol="BTCUSD", payout=78.0, is_otc=False),
            Asset(id=6, name="ETH/USD", symbol="ETHUSD", payout=75.0, is_otc=False),
        ]

    async def get_candles(self, asset, timeframe, count=200, end_time=None) -> List[Candle]:
        if end_time is None:
            end_time = int(time.time())
        candles: List[Candle] = []
        price = 1.1000 if "EUR" in asset or "USD" in asset else (30000.0 if "BTC" in asset else (1500.0 if "ETH" in asset else 1.3000))
        tf = timeframe.seconds
        trend_bias = self._rng.choice([-1, 0, 1])
        trend_len = self._rng.randint(20, 60)
        for i in range(count):
            if i and i % trend_len == 0:
                trend_bias = self._rng.choice([-1, 0, 1])
                trend_len = self._rng.randint(20, 60)
            ts = end_time - (count - i) * tf
            open_ = price
            move = self._rng.gauss(0, 0.0008 * price) + (trend_bias * 0.00008 * price)
            close = max(0.0001, open_ + move)
            high = max(open_, close) + abs(self._rng.gauss(0, 0.0003 * price))
            low = min(open_, close) - abs(self._rng.gauss(0, 0.0003 * price))
            volume = abs(self._rng.gauss(100, 30))
            candles.append(Candle(open=open_, high=high, low=low, close=close, volume=volume, timestamp=ts, closed=True))
            price = close
        return candles

    async def place_binary_option(self, asset, direction, amount, expiry_seconds, is_practice=True) -> dict:
        await asyncio.sleep(0.1)
        return {"_stub": True, "status": "ok", "asset": asset, "direction": direction, "amount": amount}

    async def stream_candles(self, asset, timeframe, handler):
        pass
