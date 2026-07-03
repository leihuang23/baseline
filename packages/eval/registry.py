"""Registry for named evaluation suites."""

from __future__ import annotations

from collections.abc import Iterable

from packages.eval.definitions import EvalSuite


class EvalRegistry:
    """In-memory suite registry used by tests and the CLI."""

    def __init__(self) -> None:
        self._suites: dict[str, EvalSuite] = {}

    def register(self, suite: EvalSuite) -> None:
        if suite.name in self._suites:
            raise ValueError(f"Evaluation suite {suite.name!r} is already registered")
        self._suites[suite.name] = suite

    def get(self, name: str) -> EvalSuite:
        try:
            return self._suites[name]
        except KeyError as exc:
            available = ", ".join(self.names())
            raise ValueError(
                f"Unknown evaluation suite {name!r}. Available suites: {available}"
            ) from exc

    def names(self) -> list[str]:
        return sorted(self._suites)

    def selected(self, names: Iterable[str] | None = None) -> list[EvalSuite]:
        if names is None:
            return [self._suites[name] for name in self.names()]
        return [self.get(name) for name in names]
