"""Every strategy the lab knows, by name.

Add a strategy by defining it in lab/strategies.py with register(). The
name is permanent: the ledger and the forward records are keyed on it, so
a changed idea gets a new name rather than an edited one.
"""
from __future__ import annotations

from lab.strategy import Strategy

_REGISTRY: dict[str, Strategy] = {}


def register(s: Strategy) -> Strategy:
    if s.name in _REGISTRY and _REGISTRY[s.name] is not s:
        raise ValueError(f"strategy {s.name!r} is already registered")
    _REGISTRY[s.name] = s
    return s


def _load() -> None:
    import lab.strategies  # noqa: F401  (registers on import)


def get(name: str) -> Strategy:
    _load()
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"no strategy {name!r}; known: {', '.join(sorted(_REGISTRY))}") from None


def all_strategies() -> list[Strategy]:
    _load()
    return sorted(_REGISTRY.values(), key=lambda s: (s.family, s.name))
