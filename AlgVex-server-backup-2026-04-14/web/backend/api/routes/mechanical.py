"""
Mechanical Strategy API Routes — v49.0 scoring data for web UI.
"""

from fastapi import APIRouter

from services.mechanical_service import (
    get_mechanical_state,
    get_signal_history,
    get_score_timeseries,
)

router = APIRouter(prefix="/public/mechanical", tags=["Mechanical"])


@router.get("/state")
async def mechanical_state():
    """
    Current mechanical decision state.

    Returns net_raw, 3-dimension scores, zone conditions,
    direction lock, and threshold configuration.
    """
    return get_mechanical_state()


@router.get("/signal-history")
async def mechanical_signal_history(limit: int = 50):
    """
    Historical mechanical signals reconstructed from feature snapshots.

    Each entry shows: timestamp, net_raw, signal, confidence tier,
    3-dimension scores, regime, and zone count.
    """
    limit = min(limit, 200)
    return get_signal_history(limit=limit)


@router.get("/score-timeseries")
async def mechanical_score_timeseries(hours: int = 24):
    """
    net_raw time series for charting.

    Returns timestamped values of net_raw plus per-dimension scores.
    """
    hours = min(hours, 168)  # max 7 days
    return get_score_timeseries(hours=hours)
