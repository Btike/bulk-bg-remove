"""Terminal output: arcade-style human logs, or JSON lines for agents."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
)

from . import __version__
from .bulk.events import Event, JSONObject, JSONValue

BANNER = (
    "[bold green] ▄▄▄ BULK-BG-REMOVE ▄▄▄[/]\n"
    f"[dim] v{__version__} · 100% local · subject pixels untouched[/]\n"
)


def _num(value: JSONValue, default: float = 0.0) -> float:
    return float(value) if isinstance(value, int | float) else default


def _str(value: JSONValue) -> str:
    return "" if value is None else str(value)


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


class JsonReporter:
    """One JSON object per line on stdout — the contract agents parse."""

    def __call__(self, event: Event) -> None:
        sys.stdout.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        sys.stdout.flush()


class TerminalReporter:
    """Human-friendly, arcade-flavoured progress log."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console(highlight=False)
        self._progress: Progress | None = None
        self._task: TaskID | None = None
        self._base: Path | None = None
        self._sources: dict[int, str] = {}

    def banner(self) -> None:
        self.console.print(BANNER)

    def header(self, phase: str, text: str) -> None:
        self.console.print(f" [bold magenta]{phase:<13}[/] {text}")

    def line(self, text: str) -> None:
        printer = self._progress.console if self._progress else self.console
        printer.print(f" {'':<13} {text}")

    def _rel(self, path: str) -> str:
        if self._base is not None:
            try:
                return str(Path(path).relative_to(self._base))
            except ValueError:
                pass
        return Path(path).name

    def __call__(self, event: Event) -> None:
        handler = getattr(self, f"_on_{event.type}", None)
        if handler is not None:
            handler(event.data)

    def close(self) -> None:
        if self._progress is not None:
            self._progress.stop()
            self._progress = None

    def _on_scan(self, data: JSONObject) -> None:
        hw = data["hardware"]
        inv = data["inventory"]
        assert isinstance(hw, dict) and isinstance(inv, dict)
        input_path = Path(_str(inv["input"]))
        self._base = input_path if input_path.is_dir() else input_path.parent
        self.header(
            "[1/4 SCAN]",
            f"{_str(hw['cpu_name'])} · {_str(hw['cpu_performance'])} perf cores · "
            f"{_num(hw['ram_available_gb']):.1f} GB free RAM · {_str(hw['device'])}",
        )
        skipped = inv["skipped"]
        skipped_count = len(skipped) if isinstance(skipped, list) else 0
        formats = inv["formats"]
        formats_text = (
            ", ".join(f"{k} {v}" for k, v in formats.items()) if isinstance(formats, dict) else ""
        )
        self.line(
            f"[bold]{_str(inv['images'])}[/] images ({formats_text}) · "
            f"{_num(inv['total_mb']):.0f} MB · {skipped_count} skipped"
        )
        if isinstance(skipped, list):
            for item in skipped[:5]:
                if isinstance(item, dict):
                    self.line(f"[dim]· {self._rel(_str(item['path']))} — {_str(item['reason'])}[/]")
            if skipped_count > 5:
                self.line(f"[dim]· … and {skipped_count - 5} more[/]")

    def _on_plan(self, data: JSONObject) -> None:
        workers = int(_num(data["workers"], 1))
        mode = "1 worker, one image at a time" if workers == 1 else f"{workers} workers in parallel"
        already = int(_num(data.get("already_done"), 0))
        todo = int(_num(data["images"]))
        self.header(
            "[2/4 PLAN]",
            f"{todo} to process · {mode} ([dim]limited by {_str(data['binding_limit'])}[/]) · "
            f"ETA ~{_duration(_num(data['eta_seconds']))}",
        )
        self.line(
            f"[dim]model {_str(data.get('model'))} · edges {_str(data.get('edges'))} · "
            f"needs {_num(data['min_free_ram_gb']):.1f} GB free RAM[/]"
        )
        if already:
            self.line(f"[dim]{already} already done — skipping (use --force to redo)[/]")
        for warning in data["warnings"] if isinstance(data["warnings"], list) else []:
            self.line(f"[yellow]! {_str(warning)}[/]")
        for blocker in data["blockers"] if isinstance(data["blockers"], list) else []:
            self.line(f"[bold red]✗ {_str(blocker)}[/]")

    def output(self, path: Path) -> None:
        self.line(f"output → [cyan]{path}[/]")

    def _on_model_download(self, data: JSONObject) -> None:
        self.line(f"[yellow]⬇ downloading model {_str(data['model'])} (first run only)…[/]")

    def _on_model_ready(self, data: JSONObject) -> None:
        self.line(f"[green]✓ model {_str(data['model'])} ready[/]")

    def start_run(self, total: int) -> None:
        self.header("[3/4 RUN]", "loading model in each worker…")
        self._progress = Progress(
            TextColumn(" " * 15),
            BarColumn(bar_width=32, complete_style="green", finished_style="bold green"),
            MofNCompleteColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=self.console,
            transient=True,
        )
        self._progress.start()
        self._task = self._progress.add_task("run", total=total)

    def _advance(self) -> None:
        if self._progress is not None and self._task is not None:
            self._progress.advance(self._task)

    def _on_worker_ready(self, data: JSONObject) -> None:
        self.line(f"[dim]worker {_str(data['worker'])} ready[/]")

    def _on_worker_failed(self, data: JSONObject) -> None:
        self.line(f"[bold red]✗ worker {_str(data['worker'])}: {_str(data['reason'])}[/]")

    def _on_job_started(self, data: JSONObject) -> None:
        self._sources[int(_num(data["job"]))] = _str(data["source"])

    def _on_job_done(self, data: JSONObject) -> None:
        source = self._sources.get(int(_num(data["job"])), "")
        self.line(
            f"[green]✓[/] {self._rel(source)}  [dim]{_str(data['width'])}×{_str(data['height'])}"
            f"  {_num(data['seconds']):.1f}s[/]"
        )
        self._advance()

    def _on_job_failed(self, data: JSONObject) -> None:
        source = self._sources.get(int(_num(data["job"])), f"job {_str(data['job'])}")
        self.line(f"[red]✗ {self._rel(source)}  {_str(data['reason'])}[/]")
        self._advance()

    def _on_paused(self, data: JSONObject) -> None:
        self.line(
            f"[bold yellow]⏸ PAUSED: free RAM {_num(data['free_ram_gb']):.1f} GB < "
            f"{_num(data['needed_gb']):.1f} GB — close some apps[/]"
        )

    def _on_resumed(self, data: JSONObject) -> None:
        self.line(f"[green]▶ RESUMED: {_num(data['free_ram_gb']):.1f} GB free[/]")

    def _on_verify_started(self, data: JSONObject) -> None:
        self.close()
        self.header("[4/4 VERIFY]", f"checking {_str(data['outputs'])} outputs…")

    def _on_finished(self, data: JSONObject) -> None:
        failed = data["failed"] if isinstance(data["failed"], list) else []
        warnings = data["warnings"] if isinstance(data["warnings"], list) else []
        status = "[bold green]GAME CLEAR[/]" if not failed else "[bold yellow]STAGE CLEAR[/]"
        self.line(
            f"{status} · {_str(data['processed'])} done · {len(failed)} failed · "
            f"{_str(data['already_done'])} already done · {_duration(_num(data['seconds']))}"
        )
        for item in warnings:
            if isinstance(item, dict):
                self.line(f"[yellow]! {self._rel(_str(item['path']))}: {_str(item['warning'])}[/]")
        for item in failed:
            if isinstance(item, dict):
                self.line(f"[red]✗ {self._rel(_str(item['path']))}: {_str(item['reason'])}[/]")
        self.line(f"output → [cyan]{_str(data['output'])}[/]")
