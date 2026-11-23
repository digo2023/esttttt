"""Self-healing utilities: retry decorators, fallback chains, watchdog."""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import Awaitable, Callable, TypeVar

from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type, before_sleep_log,
)

log = logging.getLogger(__name__)

T = TypeVar("T")


def with_retry(
    max_attempts: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 30.0,
    exceptions: tuple = (Exception,),
):
    """Decorator for async functions with self-healing retry."""
    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=base_delay, min=base_delay, max=max_delay),
            retry=retry_if_exception_type(exceptions),
            before_sleep=before_sleep_log(log, logging.WARNING),
            reraise=True,
        )
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            return await fn(*args, **kwargs)
        return wrapper
    return decorator


class CircuitBreaker:
    """Opens after N consecutive failures, blocks calls for cooldown period."""

    def __init__(self, threshold: int = 5, cooldown_sec: float = 30.0):
        self.threshold = threshold
        self.cooldown_sec = cooldown_sec
        self._failures = 0
        self._opened_at: float = 0.0
        self._open = False

    def record_success(self):
        self._failures = 0
        self._open = False

    def record_failure(self):
        self._failures += 1
        if self._failures >= self.threshold:
            self._open = True
            self._opened_at = asyncio.get_event_loop().time()
            log.error("Circuit breaker OPEN after %d failures", self._failures)

    def can_call(self) -> bool:
        if not self._open:
            return True
        elapsed = asyncio.get_event_loop().time() - self._opened_at
        if elapsed >= self.cooldown_sec:
            log.info("Circuit breaker reset — half-open")
            self._open = False
            self._failures = 0
            return True
        return False

    @property
    def state(self) -> str:
        return "open" if self._open else "closed"


class Watchdog:
    """Monitors a long-running task and restarts it if it dies."""

    def __init__(self, name: str, restart_fn: Callable[[], Awaitable[None]], check_interval: float = 5.0):
        self.name = name
        self.restart_fn = restart_fn
        self.check_interval = check_interval
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._watch_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _watch_loop(self) -> None:
        while self._running:
            try:
                if self._task is None or self._task.done():
                    if self._task and self._task.exception():
                        log.error("Watchdog '%s' task crashed: %s", self.name, self._task.exception())
                    log.info("Watchdog '%s' restarting task ...", self.name)
                    self._task = asyncio.create_task(self.restart_fn())
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.exception("Watchdog '%s' error: %s", self.name, e)
                await asyncio.sleep(self.check_interval)
