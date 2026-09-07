#!/usr/bin/env python3
"""Small dependency-free live terminal monitor for an OpenFOAM solver log.

Run inside the case over SSH:
    python3 tools/live_cfd_monitor.py

Press q to exit.  The monitor is read-only: it tails log.two-stage or log.flow
and never writes the case.
"""
from __future__ import annotations

import argparse
import math
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Deque

_TIME = re.compile(r"^Time = ([0-9.eE+-]+)s$")
_SOLVE = re.compile(
    r"Solving for (\w+), Initial residual = ([0-9.eE+-]+), Final residual = ([0-9.eE+-]+)"
)
_CONTINUITY = re.compile(
    r"time step continuity errors : sum local = ([0-9.eE+-]+), global = ([0-9.eE+-]+)"
)


@dataclass
class MonitorState:
    time_value: str = "--"
    residuals: dict[str, Deque[float]] = field(default_factory=dict)
    local_continuity: Deque[float] = field(default_factory=lambda: deque(maxlen=72))
    global_continuity: Deque[float] = field(default_factory=lambda: deque(maxlen=72))
    bounded_k: int = 0
    lines_seen: int = 0
    pending_p: float | None = None
    # Bounds grow over the monitor session; they do not roll when the 72-sample
    # display deque drops an old maximum or minimum.
    log_ranges: dict[str, tuple[float, float]] = field(default_factory=dict)

    def record(self, name: str, value: float) -> None:
        self.residuals.setdefault(name, deque(maxlen=72)).append(value)
        logged = math.log10(max(abs(value), 1e-30))
        lo, hi = self.log_ranges.get(name, (logged, logged))
        self.log_ranges[name] = min(lo, logged), max(hi, logged)

    def add(self, line: str) -> None:
        self.lines_seen += 1
        match = _TIME.match(line.strip())
        if match:
            self.time_value = match.group(1)
            return
        match = _SOLVE.search(line)
        if match:
            field_name = match.group(1)
            initial_residual = float(match.group(2))
            if field_name == "p":
                self.pending_p = initial_residual
            else:
                self.record(field_name, initial_residual)
            return
        match = _CONTINUITY.search(line)
        if match:
            if self.pending_p is not None:
                self.record("p", self.pending_p)
                self.pending_p = None
            local, global_value = float(match.group(1)), float(match.group(2))
            self.local_continuity.append(local)
            self.global_continuity.append(global_value)
            for name, value in (("C local", local), ("C global", global_value)):
                logged = math.log10(max(abs(value), 1e-30))
                lo, hi = self.log_ranges.get(name, (logged, logged))
                self.log_ranges[name] = min(lo, logged), max(hi, logged)
            return
        if "bounding k," in line:
            self.bounded_k += 1


def sparkline(values: Deque[float], width: int) -> str:
    """Return a compact log-scale history, preserving the last samples."""
    if not values or width <= 0:
        return ""
    samples = list(values)[-width:]
    transformed = [math.log10(max(value, 1e-30)) for value in samples]
    lo, hi = min(transformed), max(transformed)
    glyphs = "▁▂▃▄▅▆▇█"
    if math.isclose(lo, hi):
        return glyphs[0] * len(samples)
    return "".join(glyphs[round((value - lo) / (hi - lo) * (len(glyphs) - 1))] for value in transformed)


def two_row_bar_chart(values: Deque[float], width: int) -> tuple[str, str, float | None, float | None]:
    """Render a 16-level log-scale bar chart across two terminal rows."""
    if not values or width <= 0:
        return "", "", None, None
    samples = list(values)[-width:]
    transformed = [math.log10(max(value, 1e-30)) for value in samples]
    lo, hi = min(transformed), max(transformed)
    glyphs = " ▁▂▃▄▅▆▇█"
    top, bottom = [], []
    for value in transformed:
        level = 8 if math.isclose(lo, hi) else round((value - lo) / (hi - lo) * 16)
        level = max(0, min(16, level))
        if level <= 8:
            top.append(" ")
            bottom.append(glyphs[level])
        else:
            top.append(glyphs[level - 8])
            bottom.append("█")
    return "".join(top), "".join(bottom), lo, hi


def tail_new_lines(path: Path, offset: int) -> tuple[list[str], int]:
    """Read only newly appended complete lines; recover if the log is replaced."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return [], offset
    if size < offset:
        offset = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(offset)
        lines = handle.readlines()
        return lines, handle.tell()


def bar_chart(values: Deque[float], width: int, height: int = 4) -> tuple[list[str], float | None, float | None]:
    """Return a filled 4-row, 32-level log-magnitude bar chart."""
    if not values or width <= 0:
        return ["" for _ in range(height)], None, None
    samples = list(values)[-width:]
    transformed = [math.log10(max(abs(value), 1e-30)) for value in samples]
    lo, hi = min(transformed), max(transformed)
    glyphs = " ▁▂▃▄▅▆▇█"
    chart = [[] for _ in range(height)]
    for value in transformed:
        level = height * 4 if math.isclose(lo, hi) else round((value - lo) / (hi - lo) * height * 8)
        level = max(0, min(height * 8, level))
        for row in range(height):
            lower = (height - row - 1) * 8
            chart[row].append(glyphs[max(0, min(8, level - lower))])
    return ["".join(row) for row in chart], lo, hi


def draw(screen: curses.window, state: MonitorState, log_path: Path) -> None:
    screen.erase()
    height, width = screen.getmaxyx()
    title = f" OpenFOAM live monitor  |  {log_path}  |  q: quit "
    screen.addnstr(0, 0, title, width - 1, curses.A_REVERSE)
    if height < 37:
        screen.addnstr(3, 0, f"Resize terminal to at least 37 rows (current: {height}) for six 4-row charts.", width - 1)
        screen.refresh()
        return
    screen.addnstr(2, 0, f"solver time: {state.time_value}s    parsed lines: {state.lines_seen}", width - 1)
    screen.addnstr(4, 0, "Initial residual / continuity histories (log-scale; right edge is newest)", width - 1, curses.A_BOLD)

    row = 5
    graph_left = 14
    metrics = (
        ("Ux", state.residuals.get("Ux", deque())),
        ("Uy", state.residuals.get("Uy", deque())),
        ("Uz", state.residuals.get("Uz", deque())),
        ("p", state.residuals.get("p", deque())),
        ("C local", state.local_continuity),
        ("C global", state.global_continuity),
    )
    for name, values in metrics:
        latest = f"{values[-1]:.3e}" if values else "--"
        bars, lo, hi = bar_chart(values, max(1, width - graph_left - 1))
        scale = f"log[{lo:.1f},{hi:.1f}]" if lo is not None else "log[--,--]"
        screen.addnstr(row, 0, f"{name:<9} {latest:>11}  {scale}", width - 1, curses.A_BOLD)
        for offset, bar in enumerate(bars):
            screen.addnstr(row + 1 + offset, graph_left, bar, width - graph_left - 1)
        row += 5

    if height >= 4:
        screen.addnstr(height - 1, 0, "Read-only monitor. Flat or rising traces are not convergence.", width - 1)
    screen.refresh()


def run(screen: curses.window, log_path: Path, interval: float) -> None:
    curses.curs_set(0)
    screen.nodelay(True)
    state, offset = MonitorState(), 0
    while True:
        lines, offset = tail_new_lines(log_path, offset)
        for line in lines:
            state.add(line)
        draw(screen, state, log_path)
        key = screen.getch()
        if key in (ord("q"), ord("Q")):
            return
        time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only live OpenFOAM log monitor.")
    parser.add_argument("--log", type=Path, default=Path("log.two-stage"), help="solver log to tail")
    parser.add_argument("--interval", type=float, default=1.0, help="refresh seconds (default: 1)")
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval must be positive")
    global curses
    try:
        import curses
    except ModuleNotFoundError:
        parser.error("this terminal lacks curses; run the monitor in the Linux SSH shell")
    curses.wrapper(run, args.log, args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
