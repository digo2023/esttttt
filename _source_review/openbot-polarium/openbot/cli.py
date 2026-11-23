"""
openbot-polarium v2.0 CLI

Commands:
  login       Open browser, capture Polarium session (SSO + cookies)
  scan        Scan multiple assets, return top signals
  autotrade   Run the full auto-trading loop with TUI dashboard
  backtest    Backtest the ensemble on historical data
  train       Show learned indicator weights from past trades
  status      Show current bankroll/learning status
  assets      List tradable Polarium assets
  models      List APInex models
  reset       Reset learning state (indicator weights)
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__
from .core.analyzer import EnsembleAnalyzer
from .core.apinex_client import APInexClient, APInexError
from .core.learning import LearningEngine
from .data.browser_login import BrowserLoginCapture, STATE_FILE
from .data.live_capture import BrowserLivePolariumClient
from .data.models import Direction, Signal, Timeframe, TradeMode
from .data.polarium_client import PolariumClient, StubPolariumClient
from .execution.bankroll import BankrollConfig, BankrollManager
from .execution.executor import AutoExecutor
from .execution.scanner import Scanner
from .strategies.ensemble import evaluate_all_strategies
from .ui.dashboard import OpenBotDashboard
from .utils.helpers import env, env_bool, env_float, env_int, load_config, setup_logging

app = typer.Typer(
    name="openbot",
    help="openbot-polarium v2.0 — AI-powered autonomous binary options bot for Polarium.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console()
log = logging.getLogger("openbot.cli")


# ------------------------------------------------------------------- #
# Helpers
# ------------------------------------------------------------------- #

def _load_all():
    yaml_cfg = load_config("config.yaml", ".env")
    env_cfg = {
        "apinex_api_key": env("APINEX_API_KEY", ""),
        "apinex_base_url": env("APINEX_BASE_URL", "https://api.apinex.bond/v1"),
        "apinex_model": env("APINEX_MODEL", "gpt-4o-mini"),
        "polarium_sso_token": env("POLARIUM_SSO_TOKEN"),
        "polarium_email": env("POLARIUM_EMAIL"),
        "polarium_password": env("POLARIUM_PASSWORD"),
        "polarium_wss": env("POLARIUM_API_WSS"),
        "polarium_https": env("POLARIUM_API_HTTPS"),
        "polarium_broker_id": env_int("POLARIUM_BROKER_ID", 11),
        "trading_mode": env("TRADING_MODE", "paper"),
        "account_type": env("ACCOUNT_TYPE", "practice"),
        "initial_balance": env_float("INITIAL_BALANCE", 1000),
        "entry_value": env_float("ENTRY_VALUE", 20),
        "stop_win": env_float("STOP_WIN", 30),
        "stop_loss": env_float("STOP_LOSS", 20),
        "max_trades_per_day": env_int("MAX_TRADES_PER_DAY", 20),
        "max_concurrent": env_int("MAX_CONCURRENT_TRADES", 1),
        "max_gale_levels": env_int("MAX_GALE_LEVELS", 2),
        "use_martingale": env_bool("USE_MARTINGALE", True),
        "martingale_multiplier": env_float("MARTINGALE_MULTIPLIER", 2.0),
        "auto_execute": env_bool("AUTO_EXECUTE", False),
        "min_confidence_to_execute": env_float("MIN_CONFIDENCE_TO_EXECUTE", 0.70),
        "scan_assets": env("SCAN_ASSETS", "EURUSD-OTC,GBPUSD-OTC"),
        "scan_timeframes": env("SCAN_TIMEFRAMES", "M1,M5"),
        "scan_interval_sec": env_int("SCAN_INTERVAL_SEC", 15),
        "max_retries": env_int("MAX_RETRIES", 3),
        "learning_enabled": env_bool("LEARNING_ENABLED", True),
        "learning_window": env_int("LEARNING_WINDOW", 50),
        "learning_rate": env_float("LEARNING_RATE", 0.1),
        "revalidate_after_loss": env_bool("REVALIDATE_AFTER_LOSS", True),
        "log_level": env("LOG_LEVEL", "INFO"),
        "log_file": env("LOG_FILE", "logs/openbot.log"),
        "signals_csv": env("SIGNALS_CSV", "logs/signals.csv"),
        "trades_csv": env("TRADES_CSV", "logs/trades.csv"),
    }
    return yaml_cfg, env_cfg


def _make_polarium(env_cfg, yaml_cfg, stub=False):
    # Synthetic candles are available only when explicitly requested with --stub.\n    # Normal runs consume the authenticated browser candle feed.
    # Normal operation keeps the authenticated Polarium trader page open and
    # reads its actual WebSocket candle stream; no separate guessed WS endpoint.
    if stub:
        return StubPolariumClient()
    return BrowserLivePolariumClient(
        login_timeout=300,
        headless=False,
    )

def _make_apinex(env_cfg):
    if not env_cfg["apinex_api_key"]:
        return None
    return APInexClient(
        api_key=env_cfg["apinex_api_key"],
        base_url=env_cfg["apinex_base_url"],
        model=env_cfg["apinex_model"],
    )


def _make_analyzer(env_cfg, yaml_cfg, apinex):
    ai_cfg = yaml_cfg.get("ai", {})
    return EnsembleAnalyzer(
        apinex=apinex,
        model=env_cfg["apinex_model"],
        temperature=ai_cfg.get("temperature", 0.3),
        max_tokens=ai_cfg.get("max_tokens", 600),
        context_candles=ai_cfg.get("context_candles", 30),
        ai_enabled=ai_cfg.get("enabled", True) and apinex is not None,
    )


def _make_bankroll(env_cfg, yaml_cfg):
    t_cfg = yaml_cfg.get("trading", {})
    m_cfg = yaml_cfg.get("martingale", {})
    s_cfg = yaml_cfg.get("soros", {})
    cfg = BankrollConfig(
        initial_balance=t_cfg.get("initial_balance", env_cfg["initial_balance"]),
        entry_value=t_cfg.get("entry_value", env_cfg["entry_value"]),
        stop_win_pct=t_cfg.get("stop_win_pct", env_cfg["stop_win"]),
        stop_loss_pct=t_cfg.get("stop_loss_pct", env_cfg["stop_loss"]),
        max_trades_per_day=t_cfg.get("max_trades_per_day", env_cfg["max_trades_per_day"]),
        max_concurrent_trades=t_cfg.get("max_concurrent_trades", env_cfg["max_concurrent"]),
        martingale_enabled=m_cfg.get("enabled", env_cfg["use_martingale"]),
        martingale_levels=m_cfg.get("max_levels", env_cfg["max_gale_levels"]),
        martingale_multiplier=m_cfg.get("multiplier", env_cfg["martingale_multiplier"]),
        soros_enabled=s_cfg.get("enabled", False),
        soros_levels=s_cfg.get("levels", 1),
    )
    mode = TradeMode(t_cfg.get("mode", env_cfg["trading_mode"]))
    return BankrollManager(cfg=cfg, mode=mode)


def _make_learning(env_cfg, yaml_cfg):
    l_cfg = yaml_cfg.get("learning", {})
    db_path = l_cfg.get("db_path", "openbot_state.json")
    return LearningEngine(
        db_path=db_path,
        window=l_cfg.get("window", env_cfg["learning_window"]),
        learning_rate=l_cfg.get("rate", env_cfg["learning_rate"]),
        top_indicators=l_cfg.get("top_indicators", 8),
        revalidate_after_loss=l_cfg.get("revalidate_after_loss", env_cfg["revalidate_after_loss"]),
    )


def _fmt_signal_panel(signal, balance=None):
    color = "green" if signal.direction == Direction.CALL else (
        "red" if signal.direction == Direction.PUT else "yellow"
    )
    arrow = "↑ CALL" if signal.direction == Direction.CALL else (
        "↓ PUT" if signal.direction == Direction.PUT else "— HOLD"
    )
    title = f"[{color}]{arrow}[/{color}]  {signal.asset}  {signal.timeframe.value}"
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan")
    table.add_column()
    table.add_row("Confidence", f"{signal.confidence*100:.0f}%")
    table.add_row("Entry", f"{signal.entry_price:.5f}")
    table.add_row("Expiry", f"{signal.expiry_seconds}s")
    if balance is not None:
        table.add_row("Balance", f"${balance:,.2f}")
    table.add_row("Patterns", ", ".join(signal.patterns_detected) or "none")
    table.add_row("Top Contrib.", ", ".join(signal.top_contributors) or "—")
    table.add_row("Reasoning", Text((signal.llm_reasoning or "—")[:300], overflow="fold"))
    return Panel(table, title=title, border_style=color, expand=False)


# ------------------------------------------------------------------- #
# Commands
# ------------------------------------------------------------------- #

@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        console.print(
            f"[bold cyan]openbot-polarium[/] v{__version__}\n"
            f"AI-powered autonomous binary options bot for Polarium.\n\n"
            f"Commands: [bold]login, scan, autotrade, backtest, train, status, assets, models, reset[/]\n"
            f"Run [bold]openbot --help[/] for details."
        )


# ------------------------------------------------------------------- #
# login — browser session capture
# ------------------------------------------------------------------- #

@app.command()
def login(
    headless: bool = typer.Option(False, help="Run browser in headless mode (not recommended)"),
    timeout: int = typer.Option(300, help="Login timeout in seconds"),
):
    """Open Polarium login page in browser; capture SSO token automatically."""
    console.print("[bold cyan]Starting browser login capture...[/]")
    console.print("[dim]A Chromium window will open. Log in to Polarium normally.[/]\n")

    async def _run():
        capturer = BrowserLoginCapture(headless=headless, timeout_sec=timeout)
        try:
            session = await capturer.capture()
        except Exception as e:
            console.print(f"[red]Login capture failed: {e}[/]")
            return

        BrowserLoginCapture.save_session(session)
        BrowserLoginCapture.update_env_file(session)

        console.print(Panel(
            f"[green]✓ Session captured successfully![/]\n\n"
            f"SSO Token: [dim]{session['sso_token'][:30]}...[/]\n"
            f"Broker ID: {session.get('broker_id', 11)}\n"
            f"Cookies: {len(session.get('cookies', {}))} captured\n"
            f"Account Type: {session.get('account_type', 'practice')}\n\n"
            f"Saved to: [bold]{STATE_FILE}[/] and [bold].env[/]",
            title="Login Success", border_style="green",
        ))

    asyncio.run(_run())


# ------------------------------------------------------------------- #
# scan
# ------------------------------------------------------------------- #

@app.command()
def scan(
    assets: str = typer.Option(None, help="Comma-separated assets (default: from .env)"),
    timeframes: str = typer.Option(None, help="Comma-separated timeframes"),
    stub: bool = typer.Option(False, help="Use stub data"),
    no_llm: bool = typer.Option(False, help="Skip LLM, classical only"),
    top_n: int = typer.Option(5, help="Show top N signals"),
):
    """Scan multiple assets × timeframes and show top signals."""
    yaml_cfg, env_cfg = _load_all()
    setup_logging(env_cfg["log_level"], env_cfg["log_file"])

    assets_list = (assets or env_cfg["scan_assets"]).split(",")
    assets_list = [a.strip() for a in assets_list if a.strip()]
    tf_list = []
    for t in (timeframes or env_cfg["scan_timeframes"]).split(","):
        t = t.strip()
        if t:
            try: tf_list.append(Timeframe(t.upper()))
            except ValueError: console.print(f"[red]Bad TF: {t}[/]")

    asyncio.run(_scan_async(assets_list, tf_list, stub, no_llm, top_n, yaml_cfg, env_cfg))


async def _scan_async(assets, timeframes, stub, no_llm, top_n, yaml_cfg, env_cfg):
    pc = _make_polarium(env_cfg, yaml_cfg, stub=stub)
    apinex = _make_apinex(env_cfg) if not no_llm else None
    learning = _make_learning(env_cfg, yaml_cfg)
    if apinex:
        await apinex.__aenter__()
    try:
        analyzer = _make_analyzer(env_cfg, yaml_cfg, apinex)
        scanner = Scanner(pc, analyzer, parallel=3)
        async with pc:
            await pc.connect()
            console.print(f"[cyan]Scanning {len(assets)} assets × {len(timeframes)} TFs ...[/]")
            signals = await scanner.scan(assets, timeframes)

        t = Table(title=f"[bold]Top {min(top_n, len(signals))} Signals[/]", show_lines=True)
        t.add_column("#", style="dim")
        t.add_column("Asset")
        t.add_column("TF", style="cyan")
        t.add_column("Direction")
        t.add_column("Conf", style="bold")
        t.add_column("Top Contributor", style="magenta")
        t.add_column("Reasoning", overflow="fold")
        for i, s in enumerate(signals[:top_n], 1):
            color = "green" if s.direction == Direction.CALL else "red" if s.direction == Direction.PUT else "yellow"
            arrow = "↑" if s.direction == Direction.CALL else "↓" if s.direction == Direction.PUT else "—"
            t.add_row(
                str(i), s.asset, s.timeframe.value,
                f"[{color}]{arrow} {s.direction.value}[/{color}]",
                f"{s.confidence*100:.0f}%",
                s.top_contributors[0] if s.top_contributors else "—",
                (s.llm_reasoning or "")[:80],
            )
        console.print(t)
    finally:
        if apinex:
            await apinex.__aexit__(None, None, None)


# ------------------------------------------------------------------- #
# autotrade — full TUI dashboard
# ------------------------------------------------------------------- #

@app.command()
def autotrade(
    stub: bool = typer.Option(False, help="Use stub data"),
    no_llm: bool = typer.Option(False, help="Classical only"),
    mode: str = typer.Option(None, help="Override mode: paper | real"),
):
    """Run the full autonomous trading dashboard (TUI)."""
    yaml_cfg, env_cfg = _load_all()
    setup_logging(env_cfg["log_level"], env_cfg["log_file"])
    if mode:
        env_cfg["trading_mode"] = mode
        yaml_cfg.setdefault("trading", {})["mode"] = mode
    asyncio.run(_autotrade_async(stub, no_llm, yaml_cfg, env_cfg))


async def _autotrade_async(stub, no_llm, yaml_cfg, env_cfg):
    pc = _make_polarium(env_cfg, yaml_cfg, stub=stub)
    apinex = _make_apinex(env_cfg) if not no_llm else None
    learning = _make_learning(env_cfg, yaml_cfg)
    bankroll = _make_bankroll(env_cfg, yaml_cfg)
    if apinex:
        await apinex.__aenter__()

    assets_list = [a.strip() for a in env_cfg["scan_assets"].split(",") if a.strip()]
    tf_list = []
    for t in env_cfg["scan_timeframes"].split(","):
        t = t.strip()
        if t:
            try: tf_list.append(Timeframe(t.upper()))
            except ValueError: pass

    analyzer = _make_analyzer(env_cfg, yaml_cfg, apinex)
    scanner = Scanner(pc, analyzer, parallel=3)
    executor = AutoExecutor(
        polarium=pc, bankroll=bankroll, learning=learning,
        mode=TradeMode(env_cfg["trading_mode"]),
        auto_execute=env_cfg["auto_execute"],
        min_confidence_to_execute=env_cfg["min_confidence_to_execute"],
        trades_csv=env_cfg["trades_csv"],
    )

    async with pc:
        await pc.connect()
        console.print(f"[bold cyan]openbot-polarium v{__version__}[/]")
        console.print(f"Mode: {env_cfg['trading_mode'].upper()}  Auto-exec: {env_cfg['auto_execute']}")
        console.print(f"Assets: {assets_list}  Timeframes: {tf_list}")
        console.print("[dim]Press Q to quit, S to scan, R to toggle paper/real, E to toggle auto-exec[/]\n")

        dashboard = OpenBotDashboard(
            scanner=scanner, executor=executor, bankroll=bankroll, learning=learning,
            scan_interval_sec=env_cfg["scan_interval_sec"],
            scan_assets=assets_list, scan_timeframes=tf_list,
        )
        try:
            await dashboard.run_async()
        finally:
            if apinex:
                await apinex.__aexit__(None, None, None)


# ------------------------------------------------------------------- #
# backtest
# ------------------------------------------------------------------- #

@app.command()
def backtest(
    asset: str = typer.Option("EURUSD-OTC"),
    timeframe: str = typer.Option("M1"),
    candles: int = typer.Option(500),
    step: int = typer.Option(1),
    no_llm: bool = typer.Option(True),
    stub: bool = typer.Option(False),
):
    """Backtest the ensemble strategy on historical data."""
    yaml_cfg, env_cfg = _load_all()
    setup_logging(env_cfg["log_level"], env_cfg["log_file"])
    asyncio.run(_backtest_async(asset, timeframe, candles, step, no_llm, stub, yaml_cfg, env_cfg))


async def _backtest_async(asset, timeframe, candles_count, step, no_llm, stub, yaml_cfg, env_cfg):
    try:
        tf = Timeframe(timeframe.upper())
    except ValueError:
        console.print(f"[red]Invalid timeframe: {timeframe}[/]")
        return

    pc = _make_polarium(env_cfg, yaml_cfg, stub=stub)
    apinex = _make_apinex(env_cfg) if not no_llm else None
    bankroll = _make_bankroll(env_cfg, yaml_cfg)
    learning = _make_learning(env_cfg, yaml_cfg)
    if apinex:
        await apinex.__aenter__()

    console.print(f"[cyan]Fetching {candles_count} historical {tf.value} candles for {asset}...[/]")
    async with pc:
        await pc.connect()
        all_candles = await pc.get_candles(asset, tf, candles_count)

    if len(all_candles) < 60:
        console.print(f"[red]Not enough candles ({len(all_candles)})[/]")
        return

    analyzer = _make_analyzer(env_cfg, yaml_cfg, apinex)

    console.print(f"[cyan]Backtesting on {len(all_candles)} candles (warmup=50, step={step}, llm={'off' if no_llm else 'on'})...[/]")
    wins = losses = holds = 0
    initial_balance = bankroll.balance

    for i in range(50, len(all_candles) - 1, step):
        history = all_candles[:i+1]
        next_candle = all_candles[i+1]

        try:
            signal = await analyzer.analyze(history, asset, tf)
        except Exception as e:
            log.warning("Analysis failed at idx %d: %s", i, e)
            continue

        if not signal.is_actionable:
            holds += 1
            continue

        stake = bankroll.next_stake()
        if stake <= 0:
            break

        won = (
            (signal.direction == Direction.CALL and next_candle.close > signal.entry_price)
            or (signal.direction == Direction.PUT and next_candle.close < signal.entry_price)
        )
        bankroll.record_trade(won, stake)
        await learning.record_trade(signal.top_contributors, won)
        if won: wins += 1
        else: losses += 1

        if bankroll.stopped:
            console.print(f"[yellow]Backtest stopped: {bankroll.stop_reason}[/]")
            break

    if apinex:
        await apinex.__aexit__(None, None, None)

    total = wins + losses
    win_rate = wins / max(total, 1) * 100
    pnl = bankroll.balance - initial_balance
    pnl_pct = pnl / initial_balance * 100

    t = Table(title="[bold]Backtest Result[/]", show_lines=True)
    t.add_column("Metric", style="bold")
    t.add_column("Value", style="cyan")
    t.add_row("Asset", f"{asset} {tf.value}")
    t.add_row("Total trades", str(total))
    t.add_row("Wins / Losses", f"{wins} / {losses}")
    t.add_row("Holds (skipped)", str(holds))
    t.add_row("Win rate", f"{win_rate:.2f}%")
    t.add_row("Final balance", f"${bankroll.balance:,.2f}")
    t.add_row("P&L", f"${pnl:+,.2f} ({pnl_pct:+.2f}%)")
    t.add_row("Max consecutive losses", str(bankroll.consecutive_losses))
    t.add_row("Max consecutive wins", str(bankroll.consecutive_wins))
    console.print(t)

    # Learning state after backtest
    ls = learning.status()
    t2 = Table(title="[bold]Top Performing Indicators[/]")
    t2.add_column("Indicator")
    t2.add_column("Accuracy", style="green")
    t2.add_column("Trades", style="dim")
    t2.add_column("Weight", style="cyan")
    for p in ls["top_performers"][:8]:
        t2.add_row(p["name"], f"{p['accuracy']:.1f}%", str(p["total"]), f"{p['weight']:.3f}")
    console.print(t2)


# ------------------------------------------------------------------- #
# train — show learned weights
# ------------------------------------------------------------------- #

@app.command()
def train():
    """Show indicator/strategy accuracy learned from past trades."""
    yaml_cfg, env_cfg = _load_all()
    learning = _make_learning(env_cfg, yaml_cfg)
    s = learning.status()

    t = Table(title="[bold]Learning Engine Status[/]")
    t.add_column("Setting", style="bold")
    t.add_column("Value", style="cyan")
    for k, v in s["settings"].items():
        t.add_row(k, str(v))
    t.add_row("Total indicators tracked", str(s["total_indicators_tracked"]))
    console.print(t)

    t2 = Table(title="[bold]Top Performing Indicators[/]")
    t2.add_column("Indicator", style="bold")
    t2.add_column("Accuracy", style="green")
    t2.add_column("Total Trades", style="dim")
    t2.add_column("Weight", style="cyan")
    for p in s["top_performers"]:
        t2.add_row(p["name"], f"{p['accuracy']:.1f}%", str(p["total"]), f"{p['weight']:.3f}")
    console.print(t2)


# ------------------------------------------------------------------- #
# status
# ------------------------------------------------------------------- #

@app.command()
def status():
    """Show current bankroll and learning status (from saved state)."""
    yaml_cfg, env_cfg = _load_all()
    bankroll = _make_bankroll(env_cfg, yaml_cfg)
    learning = _make_learning(env_cfg, yaml_cfg)

    bs = bankroll.status()
    ls = learning.status()

    t = Table(title="[bold]Bankroll Status[/]")
    t.add_column("Metric", style="bold")
    t.add_column("Value", style="cyan")
    for k, v in bs.items():
        t.add_row(k, str(v))
    console.print(t)

    t2 = Table(title="[bold]Learning Status[/]")
    t2.add_column("Setting", style="bold")
    t2.add_column("Value", style="cyan")
    for k, v in ls["settings"].items():
        t2.add_row(k, str(v))
    t2.add_row("Total indicators tracked", str(ls["total_indicators_tracked"]))
    console.print(t2)

    if ls["top_performers"]:
        t3 = Table(title="[bold]Top Performing Indicators[/]")
        t3.add_column("Indicator", style="bold")
        t3.add_column("Accuracy", style="green")
        t3.add_column("Trades", style="dim")
        t3.add_column("Weight", style="cyan")
        for p in ls["top_performers"][:10]:
            t3.add_row(p["name"], f"{p['accuracy']:.1f}%", str(p["total"]), f"{p['weight']:.3f}")
        console.print(t3)


# ------------------------------------------------------------------- #
# assets / models
# ------------------------------------------------------------------- #

@app.command()
def assets(stub: bool = typer.Option(False)):
    """List tradable Polarium assets."""
    yaml_cfg, env_cfg = _load_all()
    setup_logging(env_cfg["log_level"])
    asyncio.run(_assets_async(stub, yaml_cfg, env_cfg))


async def _assets_async(stub, yaml_cfg, env_cfg):
    pc = _make_polarium(env_cfg, yaml_cfg, stub=stub)
    async with pc:
        await pc.connect()
        assets_list = await pc.get_assets()
    t = Table(title="[bold]Polarium Assets[/]")
    t.add_column("ID", style="dim")
    t.add_column("Symbol")
    t.add_column("Name")
    t.add_column("Payout", style="green")
    t.add_column("OTC", style="yellow")
    t.add_column("Min Stake", style="cyan")
    for a in assets_list:
        t.add_row(str(a.id), a.symbol, a.name, f"{a.payout:.1f}%",
                  "✓" if a.is_otc else "", f"${a.min_stake:.2f}")
    console.print(t)


@app.command()
def models():
    """List APInex models."""
    yaml_cfg, env_cfg = _load_all()
    if not env_cfg["apinex_api_key"]:
        console.print("[red]APINEX_API_KEY not set in .env[/]")
        return
    asyncio.run(_models_async(env_cfg))


async def _models_async(env_cfg):
    apinex = _make_apinex(env_cfg)
    async with apinex:
        try:
            ms = await apinex.list_models()
        except APInexError as e:
            console.print(f"[red]Failed: {e}[/]")
            return
    t = Table(title="[bold]APInex Models[/]")
    t.add_column("ID", style="cyan")
    t.add_column("Owner")
    for m in ms:
        t.add_row(m.get("id", "—"), m.get("owned_by", "—"))
    console.print(t)


# ------------------------------------------------------------------- #
# reset — clear learning state
# ------------------------------------------------------------------- #

@app.command()
def reset(
    confirm: bool = typer.Option(False, "--confirm", help="Confirm reset"),
):
    """Reset learning state (indicator weights)."""
    yaml_cfg, env_cfg = _load_all()
    db_path = yaml_cfg.get("learning", {}).get("db_path", "openbot_state.json")
    if not confirm:
        console.print(f"[yellow]This will delete {db_path}. Run with --confirm to proceed.[/]")
        return
    if Path(db_path).exists():
        Path(db_path).unlink()
        console.print(f"[green]✓ Reset learning state ({db_path} deleted)[/]")
    else:
        console.print("[dim]No state file to delete.[/]")


if __name__ == "__main__":
    app()
