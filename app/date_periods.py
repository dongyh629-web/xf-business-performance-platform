from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class WeekContext:
    anchor: pd.Timestamp
    week_start: pd.Timestamp
    week_end: pd.Timestamp
    week_cutoff: pd.Timestamp
    previous_week_start: pd.Timestamp
    previous_week_cutoff: pd.Timestamp
    iso_week: int


def week_context(anchor: pd.Timestamp) -> WeekContext:
    current = pd.Timestamp(anchor).normalize()
    week_start = current - pd.Timedelta(days=int(current.weekday()))
    week_end = week_start + pd.Timedelta(days=6)
    elapsed_days = (current - week_start).days
    previous_week_start = week_start - pd.Timedelta(days=7)
    previous_week_cutoff = previous_week_start + pd.Timedelta(days=elapsed_days)
    return WeekContext(
        anchor=current,
        week_start=week_start,
        week_end=week_end,
        week_cutoff=current,
        previous_week_start=previous_week_start,
        previous_week_cutoff=previous_week_cutoff,
        iso_week=int(current.isocalendar().week),
    )
