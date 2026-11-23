"""
Premium TUI dashboard using textual — live panels for bankroll,
open trades, signal feed, scanner results, and learning status.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    Header, Footer, Label, DataTable, RichLog, Static, TabbedContent, TabPane,
    Button, Input, Select,
)
from textual.binding import Binding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.markup import escape

from ..data.models import Direction, Signal, TradeMode
from ..execution.bankroll import BankrollManager
from ..execution.executor import AutoExecutor
from ..execution.scanner import Scanner
from ..core.analyzer import EnsembleAnalyzer
from ..core.learning import LearningEngine

log = logging.getLogger(__name__)


class StatBox(Static):
    """A small stat box in the header."""
    def __init__(self, label: str, value: str = "—", color: str = "white"):
        super().__init__()
        self.label = label
        self.value = value
        self.color = color
        self.render_box()

    def render_box(self):
        text = Text.assemble(
            (f"{self.label}\n", "dim cyan"),
            (f"{self.value}", f"bold {self.color}"),
        )
        self.update(text)

    def set_value(self, value: str, color: Optional[str] = None):
        self.value = value
        if color:
            self.color = color
        self.render_box()


class OpenBotDashboard(App):
    """Premium textual dashboard for openbot-polarium."""

    CSS = """
    Screen {
        background: #0a0e1a;
        color: #e2e8f0;
    }
    Header {
        background: #0f172a;
        color: #60a5fa;
    }
    #header-bar {
        dock: top;
        height: 4;
        background: #0f172a;
        border-bottom: solid #1e293b;
        padding: 0 1;
    }
    .stat-box {
        width: 1fr;
        height: 100%;
        border: solid #1e293b;
        padding: 1 2;
        text-align: center;
        background: #0a0e1a;
    }
    #main-area {
        height: 1fr;
    }
    #left-panel {
        width: 30%;
        border: solid #1e293b;
        padding: 1;
        background: #0a0e1a;
    }
    #right-panel {
        width: 70%;
        border: solid #1e293b;
        padding: 1;
        background: #0a0e1a;
    }
    #scanner-results {
        height: 60%;
        border: solid #1e293b;
        padding: 0;
    }
    #signal-feed {
        height: 40%;
        border: solid #1e293b;
        padding: 1;
        background: #050810;
    }
    .label-cyan {
        color: #06b6d4;
    }
    .label-green {
        color: #10b981;
    }
    .label-red {
        color: #ef4444;
    }
    .label-yellow {
        color: #f59e0b;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("s", "scan", "Scan", show=True),
        Binding("r", "toggle_mode", "Paper/Real", show=True),
        Binding("e", "toggle_exec", "Auto Exec", show=True),
        Binding("d", "dump_log", "Dump Status", show=True),
    ]

    mode = reactive("PAPER")
    auto_exec = reactive(False)

    def __init__(
        self,
        scanner: Scanner,
        executor: AutoExecutor,
        bankroll: BankrollManager,
        learning: LearningEngine,
        scan_interval_sec: int = 15,
        scan_assets: list = None,
        scan_timeframes: list = None,
    ):
        super().__init__()
        self.scanner = scanner
        self.executor = executor
        self.bankroll = bankroll
        self.learning = learning
        self.scan_interval_sec = scan_interval_sec
        self.scan_assets = scan_assets or ["EURUSD-OTC"]
        self.scan_timeframes = scan_timeframes or []
        self._scan_task: Optional[asyncio.Task] = None
        self._settle_task: Optional[asyncio.Task] = None

    def compose(self) -> ComposeResult:
        yield Header(name="openbot-polarium")
        with Horizontal(id="header-bar"):
            yield StatBox("MODE", "PAPER", "yellow")
            yield StatBox("BALANCE", "$1,000", "green")
            yield StatBox("P&L TODAY", "$0", "white")
            yield StatBox("WIN RATE", "—", "cyan")
            yield StatBox("OPEN TRADES", "0", "blue")
            yield StatBox("AUTO EXEC", "OFF", "red")
            yield StatBox("STOP", "—", "white")
        with Horizontal(id="main-area"):
            with Vertical(id="left-panel"):
                yield Label("[b cyan]BANKROLL STATUS[/]")
                yield Static(id="bankroll-info", markup=True)
                yield Label("[b cyan]LEARNING (top indicators)[/]")
                yield Static(id="learning-info", markup=True)
                yield Label("[b cyan]CONTROLS[/]")
                yield Static(
                    "[dim]S[/] = Scan now\n"
                    "[dim]R[/] = Toggle Paper/Real\n"
                    "[dim]E[/] = Toggle Auto Execute\n"
                    "[dim]Q[/] = Quit",
                    markup=True,
                )
            with Vertical(id="right-panel"):
                yield Label("[b cyan]SCANNER RESULTS[/]")
                yield DataTable(id="scanner-results")
                yield Label("[b cyan]SIGNAL FEED[/]")
                yield RichLog(id="signal-feed", markup=True, highlight=True)
        yield Footer()

    def on_mount(self) -> None:
        # Configure data table
        table = self.query_one("#scanner-results", DataTable)
        table.add_columns("Asset", "TF", "Dir", "Conf", "Stake", "Top Contributor", "Reasoning")
        # Start background loops
        self._scan_task = asyncio.create_task(self._scan_loop())
        self._settle_task = asyncio.create_task(self._settle_loop())
        self._update_header()

    async def _scan_loop(self) -> None:
        while True:
            try:
                await self.action_scan()
            except Exception as e:
                log.exception("Scan loop error: %s", e)
            await asyncio.sleep(self.scan_interval_sec)

    async def _settle_loop(self) -> None:
        while True:
            try:
                await self.executor.check_pending_settlements()
                self._update_header()
                self._update_bankroll_info()
            except Exception as e:
                log.exception("Settle loop error: %s", e)
            await asyncio.sleep(2)

    def _update_header(self) -> None:
        s = self.bankroll.status()
        boxes = self.query(StatBox)
        if len(boxes) >= 7:
            boxes[0].set_value(self.mode.value, "yellow" if self.mode == "PAPER" else "red")
            boxes[1].set_value(f"${s['balance']:,.2f}", "green")
            pnl_color = "green" if s["pnl_today"] >= 0 else "red"
            boxes[2].set_value(f"${s['pnl_today']:+.2f} ({s['pnl_today_pct']:+.1f}%)", pnl_color)
            boxes[3].set_value(f"{s['win_rate']:.1f}%", "cyan")
            boxes[4].set_value(str(len(self.executor.open_trades)), "blue")
            boxes[5].set_value("ON" if self.auto_exec else "OFF",
                               "green" if self.auto_exec else "red")
            stop_text = "STOPPED" if s["stopped"] else "ACTIVE"
            stop_color = "red" if s["stopped"] else "green"
            boxes[6].set_value(stop_text, stop_color)

    def _update_bankroll_info(self) -> None:
        s = self.bankroll.status()
        info = (
            f"Balance:       [bold green]${s['balance']:,.2f}[/]\n"
            f"Starting:      ${s['starting_balance']:,.2f}\n"
            f"P&L today:     {'[green]' if s['pnl_today']>=0 else '[red]'}${s['pnl_today']:+.2f} ({s['pnl_today_pct']:+.1f}%)[/]\n"
            f"Trades today:  {s['trades_today']} / {self.bankroll.cfg.max_trades_per_day}\n"
            f"Wins/Losses:   [green]{s['wins']}[/] / [red]{s['losses']}[/]\n"
            f"Win rate:      [cyan]{s['win_rate']:.1f}%[/]\n"
            f"Consec. loss:  [red]{s['consecutive_losses']}[/]\n"
            f"Consec. wins:  [green]{s['consecutive_wins']}[/]\n"
            f"Gale level:    [yellow]{s['gale_level']}[/]\n"
            f"Next stake:    [bold]${s['next_stake']:.2f}[/]\n"
            f"Stop Win:      [green]+{s['stop_win_pct']}%[/]\n"
            f"Stop Loss:     [red]-{s['stop_loss_pct']}%[/]\n"
            f"Status:        {'[red]STOPPED: ' + s['stop_reason'] + '[/]' if s['stopped'] else '[green]ACTIVE[/]'}"
        )
        self.query_one("#bankroll-info").update(info)

        # Learning info
        ls = self.learning.status()
        top_text = "\n".join(
            f"{p['name']:20} acc={p['accuracy']:5.1f}%  w={p['weight']:.2f}  n={p['total']}"
            for p in ls["top_performers"][:8]
        ) or "[dim]No data yet — run scan to start collecting[/]"
        self.query_one("#learning-info").update(top_text)

    async def action_scan(self) -> None:
        """Run one scan cycle."""
        if not self.scan_timeframes:
            return
        self.query_one("#signal-feed").write("[dim cyan]Scanning...[/]")
        signals = await self.scanner.scan(self.scan_assets, self.scan_timeframes)

        # Update scanner table
        table = self.query_one("#scanner-results", DataTable)
        table.clear()
        for s in signals[:20]:
            dir_color = "green" if s.direction == Direction.CALL else "red" if s.direction == Direction.PUT else "yellow"
            stake = self.bankroll.next_stake() if s.is_actionable else 0
            top_contrib = s.top_contributors[0] if s.top_contributors else "—"
            reasoning = (s.llm_reasoning or "")[:50]
            table.add_row(
                s.asset,
                s.timeframe.value,
                Text(s.direction.value, style=dir_color),
                f"{s.confidence*100:.0f}%",
                f"${stake:.2f}" if stake else "—",
                top_contrib,
                reasoning,
            )

        # Auto-execute best signal
        best = signals[0] if signals else None
        if best and best.is_actionable and best.confidence >= self.executor.min_confidence_to_execute:
            arrow = "↑" if best.direction == Direction.CALL else "↓"
            color = "green" if best.direction == Direction.CALL else "red"
            self.query_one("#signal-feed").write(
                f"[{color}]★ {arrow} {best.direction.value}[/{color}] "
                f"{best.asset} {best.timeframe.value} conf={best.confidence*100:.0f}% "
                f"— executing..."
            )
            trade = await self.executor.execute(best)
            if trade:
                self.query_one("#signal-feed").write(
                    f"[bold]Trade opened:[/] {trade.id} stake=${trade.stake:.2f} "
                    f"gale=L{trade.gale_level}"
                )
        elif best:
            self.query_one("#signal-feed").write(
                f"[dim]Best signal {best.direction.value} conf={best.confidence*100:.0f}% "
                f"— below threshold ({self.executor.min_confidence_to_execute*100:.0f}%)[/]"
            )
        else:
            self.query_one("#signal-feed").write("[dim]No actionable signals[/]")

        self._update_header()
        self._update_bankroll_info()

    def action_toggle_mode(self) -> None:
        if self.mode == "PAPER":
            self.mode = "REAL"
        else:
            self.mode = "PAPER"
        from ..data.models import TradeMode
        self.executor.mode = TradeMode(self.mode.value)
        self.query_one("#signal-feed").write(
            f"[bold yellow]Mode switched to {self.mode.value}[/]"
        )
        self._update_header()

    def action_toggle_exec(self) -> None:
        self.auto_exec = not self.auto_exec
        self.executor.auto_execute = self.auto_exec
        self.query_one("#signal-feed").write(
            f"[bold {'green' if self.auto_exec else 'red'}]"
            f"Auto-execute {'ENABLED' if self.auto_exec else 'DISABLED'}[/]"
        )
        self._update_header()

    def action_dump_log(self) -> None:
        s = self.bankroll.status()
        es = self.executor.status()
        ls = self.learning.status()
        msg = (
            f"[bold cyan]=== STATUS DUMP ===[/]\n"
            f"Bankroll: {s}\n\n"
            f"Executor: {es}\n\n"
            f"Learning: {ls}\n"
        )
        self.query_one("#signal-feed").write(msg)
