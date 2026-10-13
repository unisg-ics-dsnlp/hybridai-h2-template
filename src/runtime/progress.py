"""Progress interface for callers; runtime code does not depend on terminal UI."""
from __future__ import annotations

from typing import Protocol


class ProgressReporter(Protocol):
    def start(self, label: str, total: int | None = None,
              estimate: float | None = None, unit: str | None = None) -> None: ...
    def step(self, count: int = 1) -> None: ...
    def finish(self, note: str = "") -> float: ...
    def message(self, text: str) -> None: ...
    def close(self) -> None: ...


class NullProgress:
    """Keep library sessions silent unless the caller supplies a reporter."""

    def start(self, label: str, total: int | None = None,
              estimate: float | None = None, unit: str | None = None) -> None:
        pass

    def step(self, count: int = 1) -> None:
        pass

    def finish(self, note: str = "") -> float:
        return 0.0

    def message(self, text: str) -> None:
        pass

    def close(self) -> None:
        pass
