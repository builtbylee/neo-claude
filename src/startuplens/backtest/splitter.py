"""Walk-forward time-window splitter for backtest validation.

Generates expanding-window splits so the model is tested on strictly
future data at every step.  The five canonical windows match the
architecture spec (Backtesting Plan 0a).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True)
class TimeWindow:
    """A single train/test split defined by date boundaries."""

    train_start: date
    train_end: date
    test_start: date
    test_end: date
    label: str

    def __str__(self) -> str:
        return self.label


# ---------------------------------------------------------------------------
# Walk-forward window generation
# ---------------------------------------------------------------------------

_CANONICAL_WINDOWS: list[tuple[date, date, date, date, str]] = [
    (date(2016, 1, 1), date(2018, 12, 31), date(2019, 1, 1), date(2019, 12, 31),
     "Train 2016-2018, Test 2019"),
    (date(2016, 1, 1), date(2019, 12, 31), date(2020, 1, 1), date(2020, 12, 31),
     "Train 2016-2019, Test 2020"),
    (date(2016, 1, 1), date(2020, 12, 31), date(2021, 1, 1), date(2021, 12, 31),
     "Train 2016-2020, Test 2021"),
    (date(2016, 1, 1), date(2021, 12, 31), date(2022, 1, 1), date(2022, 12, 31),
     "Train 2016-2021, Test 2022"),
    (date(2016, 1, 1), date(2022, 12, 31), date(2023, 1, 1), date(2025, 12, 31),
     "Train 2016-2022, Test 2023-2025"),
]


def generate_walk_forward_windows() -> list[TimeWindow]:
    """Return the five canonical expanding walk-forward windows."""
    return [
        TimeWindow(
            train_start=ts,
            train_end=te,
            test_start=vs,
            test_end=ve,
            label=label,
        )
        for ts, te, vs, ve, label in _CANONICAL_WINDOWS
    ]


# ---------------------------------------------------------------------------
# Entity splitting
# ---------------------------------------------------------------------------


def _extract_campaign_date(entity: Any) -> date:
    """Extract a ``date`` from an entity's ``campaign_date`` field.

    Accepts:
    - ``datetime.date`` instances
    - ISO-format date strings (``"YYYY-MM-DD"``)
    - Objects with a ``.date()`` method (e.g. ``datetime.datetime``)
    """
    raw = entity.campaign_date if hasattr(entity, "campaign_date") else entity["campaign_date"]
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        return date.fromisoformat(raw)
    # datetime objects
    return raw.date()


def split_entities_by_window(
    entities: Sequence[Any],
    window: TimeWindow,
) -> tuple[list[Any], list[Any]]:
    """Partition *entities* into (train, test) lists based on *window* boundaries.

    An entity falls into the **train** set if its ``campaign_date`` is within
    ``[window.train_start, window.train_end]`` (inclusive on both ends).

    It falls into the **test** set if its ``campaign_date`` is within
    ``[window.test_start, window.test_end]`` (inclusive on both ends).

    Entities whose ``campaign_date`` falls outside both ranges are silently
    excluded.
    """
    train: list[Any] = []
    test: list[Any] = []
    for entity in entities:
        d = _extract_campaign_date(entity)
        if window.train_start <= d <= window.train_end:
            train.append(entity)
        elif window.test_start <= d <= window.test_end:
            test.append(entity)
    return train, test
