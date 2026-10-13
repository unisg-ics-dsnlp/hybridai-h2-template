"""A small progress reporter for the loading phases.

Loading a logic base has two phases with very different shapes:

  * Staging the program is chunked and streamed from disk. File sizes provide the
    byte total without reading or retaining the generated program up front, so
    the bar advances with the bytes consumed while memory stays bounded.

  * Building indexes and deriving the closure is one blocking commit into the
    engine with no progress reporting of its own. The elapsed time is shown
    without predicting its duration from the much cheaper staging phase.

A background ticker repaints the line a few times a second, which is what lets
the elapsed time keep moving during that blocking call without threading the
engine request itself.

Everything goes to stderr, so `--query` output stays clean when piped. When
stderr is not a terminal the animation is skipped and each phase prints one
plain line instead.
"""

from __future__ import annotations

import sys
import threading
import time

BAR_WIDTH = 24
FILLED, EMPTY = "█", "░"
TICK_SECONDS = 0.1


def human(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes}m{rest:02d}s"


class Progress:
    """Renders one phase at a time to stderr.

    Safe to use when disabled: every method becomes a no-op, so callers do not
    need to branch on whether progress is being shown.
    """

    def __init__(self, enabled: bool | None = None, stream=None):
        self.stream = stream or sys.stderr
        if enabled is None:
            enabled = bool(getattr(self.stream, "isatty", lambda: False)())
        self.enabled = enabled
        self._label = ""
        self._total: int | None = None
        self._done = 0
        self._unit: str | None = None
        self._estimate: float | None = None
        self._started = 0.0
        self._lock = threading.Lock()
        self._ticker: threading.Thread | None = None
        self._stop = threading.Event()
        self._width = 0

    # ------------------------------------------------------------------
    # Phases
    # ------------------------------------------------------------------

    def start(self, label: str, total: int | None = None,
              estimate: float | None = None, unit: str | None = None) -> None:
        """Begin a phase. `total` gives a counted bar, `estimate` an ETA."""
        with self._lock:
            self._label, self._total, self._done = label, total, 0
            self._estimate = estimate
            self._unit = unit
            self._started = time.monotonic()
        if not self.enabled:
            suffix = f" (~{human(estimate)})" if estimate else ""
            print(f"{label}...{suffix}", file=self.stream, flush=True)
            return
        self._stop.clear()
        self._ticker = threading.Thread(target=self._tick, daemon=True)
        self._ticker.start()

    def step(self, count: int = 1) -> None:
        with self._lock:
            self._done += count
        if self.enabled:
            self._paint()

    def finish(self, note: str = "") -> float:
        """End the phase, leaving one completed line behind. Returns elapsed."""
        elapsed = time.monotonic() - self._started
        self._halt()
        line = f"  {self._label}: {human(elapsed)}"
        if note:
            line += f" — {note}"
        if self.enabled:
            self._clear()
        print(line, file=self.stream, flush=True)
        return elapsed

    def message(self, text: str) -> None:
        """Print a line without disturbing an active bar."""
        if self.enabled:
            self._clear()
        print(text, file=self.stream, flush=True)

    def close(self) -> None:
        self._halt()
        if self.enabled:
            self._clear()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _halt(self) -> None:
        self._stop.set()
        if self._ticker and self._ticker.is_alive():
            self._ticker.join(timeout=TICK_SECONDS * 3)
        self._ticker = None

    def _tick(self) -> None:
        while not self._stop.wait(TICK_SECONDS):
            self._paint()

    def _clear(self) -> None:
        if self._width:
            self.stream.write("\r" + " " * self._width + "\r")
            self.stream.flush()
            self._width = 0

    def _paint(self) -> None:
        with self._lock:
            label, total, done, unit = self._label, self._total, self._done, self._unit
            estimate, started = self._estimate, self._started
        elapsed = time.monotonic() - started

        if total:
            fraction = min(1.0, done / total)
            # Projected from the observed rate, so it sharpens as it runs.
            remaining = (elapsed / done) * (total - done) if done else estimate
            tail = f"{fraction:.0%}" if unit == "bytes" else f"{done}/{total}"
            filled = int(round(fraction * BAR_WIDTH))
        else:
            # Nothing to count, so the bar tracks an estimate. Keep at least
            # one cell empty: a full bar on work that is still running reads as
            # finished, and this phase only ends when the engine returns.
            fraction = min(1.0, elapsed / estimate) if estimate else 0.0
            remaining = max(0.0, estimate - elapsed) if estimate else None
            tail = "working"
            filled = min(BAR_WIDTH - 1, int(round(fraction * BAR_WIDTH)))
        bar = FILLED * filled + EMPTY * (BAR_WIDTH - filled)
        line = f"  {label} [{bar}] {tail} · {human(elapsed)}"
        if remaining and remaining > 0.5:
            line += f" · ~{human(remaining)} left"

        self._clear()
        self.stream.write(line)
        self.stream.flush()
        self._width = len(line)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
