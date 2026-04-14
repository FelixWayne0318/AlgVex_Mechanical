"""
v4.0: S/R Pivot Point Calculator (Projection Layer)

Floor Trader Pivot Points (Daily + Weekly) for projecting future S/R levels.
Especially useful in ATH scenarios where no historical resistance exists above.

Reference: Floor Trader's Pivot (standard), CME Market Profile
"""

import logging
from typing import Dict, List, Optional, Any

from utils.sr_types import SRCandidate, SRLevel, SRSourceType


logger = logging.getLogger(__name__)


def calculate_pivots(
    daily_bar: Optional[Dict[str, Any]],
    weekly_bar: Optional[Dict[str, Any]],
    current_price: float,
) -> List[SRCandidate]:
    """
    Floor Trader Pivot Points (Daily + Weekly).

    Daily: Calculated from most recent completed daily bar.
    Weekly: Calculated from most recent completed weekly bar (covers multi-day breakout scenarios).

    All Pivot candidates are marked source_type=PROJECTED, strength capped at MEDIUM.
    AI report annotates: "PROJECTED - mathematical projection, no historical trade confirmation"

    Parameters
    ----------
    daily_bar : Optional[Dict]
        Most recent completed daily bar with 'high', 'low', 'close' keys.
    weekly_bar : Optional[Dict]
        Most recent completed weekly bar with 'high', 'low', 'close' keys.
        Can be aggregated from 5 most recent daily bars.
    current_price : float
        Current market price for support/resistance classification.

    Returns
    -------
    List[SRCandidate]
        Pivot point candidates.
    """
    candidates = []

    for bar, period, base_weight, tf in [
        (daily_bar, 'Daily', 1.0, 'daily_pivot'),
        (weekly_bar, 'Weekly', 1.2, 'weekly_pivot'),
    ]:
        if not bar:
            continue
        H = float(bar.get('high', 0))
        L = float(bar.get('low', 0))
        C = float(bar.get('close', 0))
        if H <= 0 or L <= 0 or C <= 0:
            continue

        PP = (H + L + C) / 3
        pivots = {
            'PP': PP,
            'R1': 2 * PP - L,
            'R2': PP + (H - L),
            'R3': H + 2 * (PP - L),
            'S1': 2 * PP - H,
            'S2': PP - (H - L),
            'S3': L - 2 * (H - PP),
        }

        for name, price in pivots.items():
            if price <= 0:
                continue
            side = 'support' if price < current_price else 'resistance'
            candidates.append(SRCandidate(
                price=price,
                source=f"{period}Pivot_{name}",
                weight=base_weight,
                side=side,
                level=SRLevel.MAJOR,
                source_type=SRSourceType.PROJECTED,
                timeframe=tf,
            ))

    return candidates


def aggregate_weekly_bar(daily_bars: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Aggregate up to 5 most recent daily bars into a weekly bar.

    Parameters
    ----------
    daily_bars : List[Dict]
        Recent daily bars (oldest first). At least 1 bar required.

    Returns
    -------
    Optional[Dict]
        Weekly bar with 'high', 'low', 'close' keys, or None.
    """
    if not daily_bars:
        return None

    recent = daily_bars[-5:]  # Last 5 trading days
    try:
        high = max(float(b.get('high', 0)) for b in recent)
        # v2.0 Phase 1: Bug fix — float('inf') default causes hidden logic error
        low_vals = [float(b.get('low', 0)) for b in recent if float(b.get('low', 0)) > 0]
        low = min(low_vals) if low_vals else 0
        close = float(recent[-1].get('close', 0))

        if high <= 0 or low <= 0 or close <= 0:
            return None

        return {'high': high, 'low': low, 'close': close}
    except (ValueError, TypeError):
        return None


def calculate_pivots_pandas_ta(
    bar: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    pandas-ta pivot cross-validation against hand-written Floor Trader Pivots.

    Accepts a single bar dict with 'high', 'low', 'close' keys.
    Returns a dict with pandas-ta pivot results, or None if pandas-ta
    is not installed or computation fails.

    Usage example::

        hw = calculate_pivots(daily_bar, None, current_price)
        pta = calculate_pivots_pandas_ta(daily_bar)
        # Compare hw[0].price (PP) with pta pivot point value.
    """
    try:
        import pandas as pd
        import pandas_ta as ta

        df = pd.DataFrame([bar])
        df.index = pd.DatetimeIndex([pd.Timestamp.now()])
        result = ta.pivots(df["high"], df["low"], df["close"])
        if result is not None:
            return result.to_dict()
    except (ImportError, Exception):
        pass
    return None
