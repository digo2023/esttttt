"""Live candle feed captured from the authenticated Polarium Traderoom browser session.

This adapter deliberately does not open orders and does not fabricate market data.\nIt listens to the trader page's own authenticated WebSocket connection and correlates historical replies to the chart's asset and timeframe.
It consumes the WebSocket frames produced by the trader page itself.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from .models import Asset, Candle, Timeframe

log = logging.getLogger(__name__)


class BrowserLivePolariumClient:
    """Keep the Polarium trader page open and collect its real candle frames."""

    def __init__(self, login_url: str = "https://trade.polariumbroker.com/pt/login?redirect_url=traderoom%2F",
                 trader_url: str = "https://trade.polariumbroker.com/traderoom",
                 headless: bool = False, login_timeout: int = 300):
        self.login_url = login_url
        self.trader_url = trader_url
        self.headless = headless
        self.login_timeout = login_timeout
        self._playwright = self._browser = self._context = self._page = None
        self._candles: Dict[Tuple[str, int], Dict[int, Candle]] = defaultdict(dict)
        self._assets: Dict[str, Asset] = {}
        self._id_to_name: Dict[str, str] = {}
        self._request_context: Dict[str, dict] = {}
        self._connected = False
        self._last_frame = 0.0
        self._frame_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def connect(self):
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("Instale Playwright: pip install playwright && playwright install chromium") from exc

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        self._context = await self._browser.new_context(
            viewport={"width": 1365, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
        )
        self._page = await self._context.new_page()
        self._page.on("websocket", self._on_websocket)
        print("\nPOLARIUM — CAPTURA DE CANDLES REAIS")
        print("Entre na conta pelo navegador aberto e deixe o gráfico do ativo desejado carregado.")
        await self._page.goto(self.login_url, wait_until="domcontentloaded")
        print(f"Login: {self.login_url}")
        print(f"Sala de negociação: {self.trader_url}")
        try:
            await self._page.wait_for_url(
                lambda url: "trade.polariumbroker.com" in str(url).lower()
                and "/traderoom" in str(url).lower(),
                timeout=self.login_timeout * 1000,
            )
        except Exception:
            if "/login" in self._page.url.lower():
                raise RuntimeError("Login Polarium não foi concluído. Conclua o login no navegador aberto.")
        if "/traderoom" not in self._page.url.lower():
            await self._page.goto(self.trader_url, wait_until="domcontentloaded")
        await self._page.wait_for_timeout(8000)
        self._connected = True
        log.info("Sala de negociação aberta; aguardando frames reais de candles.")
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline and not any(self._candles.values()):
            await self._page.wait_for_timeout(500)
        candle_count = sum(len(v) for v in self._candles.values())
        if not candle_count:
            raise RuntimeError(
                "A sala abriu, mas nenhum candle foi recebido pelo WebSocket. "
                "Deixe um gráfico carregado na Polarium e confira se a conexão está ativa."
            )
        log.info("Candles recebidos do WebSocket: %d", candle_count)
        return self

    def _on_websocket(self, ws):
        url = getattr(ws, "url", "")
        if "polarium" not in url.lower() and "quadcode" not in url.lower():
            return
        log.info("WebSocket do trader detectado: %s", url.split("?")[0])
        ws.on("framereceived", lambda payload: self._schedule_frame(payload, "received"))
        ws.on("framesent", lambda payload: self._schedule_frame(payload, "sent"))

    def _schedule_frame(self, payload, direction="received"):
        try:
            asyncio.get_running_loop().create_task(self._consume_frame(payload, direction))
        except RuntimeError:
            pass

    async def _consume_frame(self, payload, direction="received"):
        if isinstance(payload, bytes):
            try:
                payload = payload.decode("utf-8", "ignore")
            except Exception:
                return
        if not isinstance(payload, str):
            return
        raw = payload.strip()
        if raw.startswith("42"):
            raw = raw[2:]
        try:
            data = json.loads(raw)
        except Exception:
            # Ignore heartbeat / non-JSON transport frames.
            return
        self._frame_count += 1
        self._last_frame = time.time()
        request_id = self._find_value(data, "request_id")
        if direction == "sent":
            active_id = self._find_value(data, "active_id") or self._find_value(data, "asset_id")
            size = self._find_value(data, "size")
            if request_id is not None and active_id is not None:
                self._request_context[str(request_id)] = {
                    "active_id": active_id,
                    **({"size": size} if size is not None else {}),
                }
            return
        inherited = self._request_context.get(str(request_id), {}) if request_id is not None else {}
        # Socket.IO event frames are arrays: [event_name, payload].
        if isinstance(data, list) and len(data) >= 2 and isinstance(data[0], str):
            self._walk(data[1], inherited, data[0])
        else:
            self._walk(data, inherited, "live")

    @staticmethod
    def _find_value(obj, wanted):
        if isinstance(obj, dict):
            if wanted in obj and obj[wanted] is not None:
                return obj[wanted]
            for value in obj.values():
                found = BrowserLivePolariumClient._find_value(value, wanted)
                if found is not None:
                    return found
        elif isinstance(obj, list):
            for value in obj:
                found = BrowserLivePolariumClient._find_value(value, wanted)
                if found is not None:
                    return found
        return None

    def _walk(self, obj, inherited, event):
        if isinstance(obj, list):
            for item in obj:
                self._walk(item, inherited, event)
            return
        if not isinstance(obj, dict):
            return

        ctx = dict(inherited)
        for key in ("active_id", "asset_id", "size", "timeframe", "phase", "name", "ticker", "symbol"):
            if key in obj:
                ctx[key] = obj[key]

        name = str(obj.get("name", event))
        msg = obj.get("msg", obj.get("data", obj))
        if isinstance(msg, (dict, list)) and msg is not obj:
            self._walk(msg, ctx, name)

        self._register_asset(obj)
        if self._is_candle(obj):
            self._store_candle(obj, ctx, name)

        for key, value in obj.items():
            if key not in ("msg", "data"):
                if isinstance(value, (dict, list)):
                    child_ctx = dict(ctx)
                    if key.isdigit():
                        child_ctx.setdefault("size", int(key))
                    self._walk(value, child_ctx, name)

    @staticmethod
    def _is_candle(obj):
        return all(k in obj for k in ("open", "close")) and (
            ("high" in obj or "max" in obj) and ("low" in obj or "min" in obj)
        )

    def _store_candle(self, item, ctx, event):
        active_id = item.get("active_id", ctx.get("active_id", ctx.get("asset_id")))
        size = item.get("size", ctx.get("size", ctx.get("timeframe")))
        if active_id is None or size is None:
            return
        try:
            size = int(size)
            # Quadcode candle size may be seconds or enum (1=M1, 2=M5...).
            size = {1: 60, 2: 300, 3: 900, 4: 1800, 5: 3600}.get(size, size)
            ts = item.get("from", item.get("timestamp", item.get("at")))
            ts = int(float(ts))
            if ts > 10**17:
                ts //= 10**9
            elif ts > 10**14:
                ts //= 10**6
            elif ts > 10**11:
                ts //= 10**3
            op = float(item["open"])
            cl = float(item["close"])
            hi = float(item.get("max", item.get("high")))
            lo = float(item.get("min", item.get("low")))
            vol = float(item.get("volume", 0) or 0)
        except (TypeError, ValueError, KeyError):
            return
        phase = str(item.get("phase", ctx.get("phase", ""))).upper()
        # Polarium's phase T is an in-progress candle. History batches are closed.
        event_lower = str(event).lower()
        is_live_update = ("candle-generated" in event_lower or "candles-generated" in event_lower or phase == "T")
        closed = bool(item.get("closed", not is_live_update))
        if phase == "T":
            closed = False
        series = self._candles[(str(active_id), size)]
        # When a newer bar starts, the previous in-progress bar is now confirmed.
        for previous_ts, previous in list(series.items()):
            if previous_ts < ts and not previous.closed:
                previous.closed = True
        candle = Candle(open=op, high=hi, low=lo, close=cl, volume=vol,
                        timestamp=ts, closed=closed)
        series[ts] = candle

    def _register_asset(self, obj):
        aid = obj.get("active_id", obj.get("id"))
        name = obj.get("ticker", obj.get("name", obj.get("symbol")))
        if aid is None or not isinstance(name, str) or not name:
            return
        self._id_to_name[str(aid)] = name
        try:
            self._assets[name] = Asset(
                id=int(aid), name=name, symbol=name, payout=0.0,
                is_otc="OTC" in name.upper(), active=True,
            )
        except (TypeError, ValueError):
            pass

    async def get_assets(self) -> List[Asset]:
        return list(self._assets.values())

    async def get_candles(self, asset, timeframe: Timeframe, count: int = 200, end_time=None) -> List[Candle]:
        tf_seconds = timeframe.seconds
        asset_text = str(asset)
        ids = [aid for aid, name in self._id_to_name.items()
               if name.casefold() == asset_text.casefold()]
        if asset_text.isdigit():
            ids.append(asset_text)
        found = []
        for aid in dict.fromkeys(ids):
            found.extend(self._candles.get((aid, tf_seconds), {}).values())
        found.sort(key=lambda candle: candle.timestamp)
        if end_time is not None:
            found = [c for c in found if c.timestamp <= int(end_time)]
        # The strategy engine receives completed candles only; live candle stays in cache
        # but is excluded until the next timestamp arrives / source marks it closed.
        closed = [c for c in found if c.closed]
        return closed[-count:]

    async def stream_candles(self, asset, timeframe, handler):
        key = (str(asset), timeframe.seconds)
        seen = set()
        while self._connected:
            for candle in await self.get_candles(asset, timeframe, 500):
                marker = (candle.timestamp, candle.close)
                if marker not in seen:
                    seen.add(marker)
                    await handler(candle)
            await asyncio.sleep(0.25)

    async def close(self):
        self._connected = False
        try:
            if self._browser:
                await self._browser.close()
        finally:
            if self._playwright:
                await self._playwright.stop()
        self._browser = self._context = self._page = self._playwright = None

    async def place_binary_option(self, *args, **kwargs):
        raise RuntimeError("Execução de ordens desativada: este cliente apenas captura candles.")
