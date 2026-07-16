"""Shared display formatting helpers."""
from __future__ import annotations
import math


def aum_breakpoints(min_val: float, max_val: float, steps: int = 40) -> list[float]:
    """Log-spaced AUM breakpoints for a select_slider — AUM spans from
    thousands to trillions in this dataset, so a linear slider would put
    almost every real prospect in the first sliver of the track."""
    if min_val >= max_val:
        return [min_val, max_val]
    lo = max(min_val, 1.0)
    hi = max(max_val, lo * 1.01)
    log_lo, log_hi = math.log10(lo), math.log10(hi)
    points = [10 ** (log_lo + (log_hi - log_lo) * i / (steps - 1)) for i in range(steps)]
    points[0] = min_val
    points[-1] = max_val
    return points


def format_aum(value) -> str:
    """Human-readable AUM: $498.6M, $1.2B. Empty string for missing values."""
    if value is None:
        return ""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return ""
    if value != value:  # NaN
        return ""
    if value >= 1_000_000_000_000:
        return f"${value / 1_000_000_000_000:.1f}T"
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.1f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    return f"${value:,.0f}"
