from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator

_CONTROLLED_EXECUTION: ContextVar[bool] = ContextVar(
    "controlled_execution", default=False
)


@contextmanager
def controlled_execution() -> Iterator[None]:
    token = _CONTROLLED_EXECUTION.set(True)
    try:
        yield
    finally:
        _CONTROLLED_EXECUTION.reset(token)


def is_controlled_execution() -> bool:
    return _CONTROLLED_EXECUTION.get()
