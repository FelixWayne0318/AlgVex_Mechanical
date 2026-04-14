"""
v5.0: S/R Volume Profile Calculator (Confirmation Layer)

Range Uniform Distribution volume profile using execution layer bars (24h lookback).
Independent data source from detection layer (1D/4H swing points).
v18.2: Migrated from 15M to 30M bars (48 bars = 24h).
v5.0: Replaced hand-written histogram with numpy.histogram. Removed dead-code
      TPO and combined_profile functions (never called outside this file).

Produces VPOC (Volume Point of Control), VAH (Value Area High), VAL (Value Area Low).

Reference: CME Market Profile, SHS (2021) — VPOC 90% reaction rate (WIG20)
"""

import logging
from typing import Dict, List, Any

import numpy as np

from utils.sr_types import SRCandidate, SRLevel, SRSourceType


logger = logging.getLogger(__name__)


def calculate_volume_profile(
    bars: List[Dict[str, Any]],
    current_price: float,
    value_area_pct: int = 70,
    min_bins: int = 30,
    max_bins: int = 80,
) -> List[SRCandidate]:
    """
    Calculate Volume Profile using Range Uniform Distribution.

    For each bar, volume is distributed proportionally across price bins based
    on the overlap between the bar's H-L range and each bin — avoiding the
    close-only bias of simple volume profiling.

    Parameters
    ----------
    bars : List[Dict]
        Execution layer bars (30M: ~48 bars = 24h). Each must have 'high', 'low', 'volume'.
    current_price : float
        Current market price (must be positive).
    value_area_pct : int
        Value Area percentage (standard: 70%).
    min_bins, max_bins : int
        Bin count bounds.

    Returns
    -------
    List[SRCandidate]
        VPOC, VAH, VAL candidates.
    """
    if not bars or len(bars) < 10 or current_price <= 0:
        if current_price <= 0:
            logger.warning(f"Volume Profile: current_price={current_price} <= 0, skipping")
        return []

    try:
        highs = np.array([float(b.get('high', 0)) for b in bars], dtype=float)
        lows = np.array([float(b.get('low', 0)) for b in bars], dtype=float)
        volumes = np.array([float(b.get('volume', 0)) for b in bars], dtype=float)

        valid = (highs > 0) & (lows > 0) & (volumes > 0)
        if not valid.any():
            return []

        highs, lows, volumes = highs[valid], lows[valid], volumes[valid]

        price_high, price_low = float(highs.max()), float(lows.min())
        price_range = price_high - price_low
        if price_range <= 0:
            return []

        num_bins = max(min_bins, min(max_bins, int(price_range / (current_price * 0.001))))
        bin_edges = np.linspace(price_low, price_high, num_bins + 1)
        vol_bins = np.zeros(num_bins, dtype=float)

        # Vectorized Range Uniform Distribution: distribute each bar's volume
        # proportionally across bins by H-L overlap
        for h, l, vol in zip(highs, lows, volumes):
            bar_range = h - l
            if bar_range > 0:
                overlap = (np.minimum(h, bin_edges[1:]) - np.maximum(l, bin_edges[:-1])) / bar_range
            else:
                # Doji: assign volume to the single containing bin
                overlap = np.zeros(num_bins)
                idx = min(int(np.searchsorted(bin_edges[:-1], l, side='right') - 1), num_bins - 1)
                overlap[max(idx, 0)] = 1.0
            vol_bins += vol * np.maximum(overlap, 0)

        total_volume = float(vol_bins.sum())
        if total_volume <= 0:
            return []

        # VPOC: bin with highest volume
        vpoc_idx = int(np.argmax(vol_bins))
        vpoc_price = float((bin_edges[vpoc_idx] + bin_edges[vpoc_idx + 1]) / 2)

        # Value Area: accumulate top-volume bins until value_area_pct reached
        sorted_bins = np.argsort(vol_bins)[::-1]
        va_cumsum = np.cumsum(vol_bins[sorted_bins])
        n_va = int(np.searchsorted(va_cumsum, total_volume * (value_area_pct / 100.0))) + 1
        va_indices = sorted(sorted_bins[:n_va].tolist())
        vah_price = float(bin_edges[va_indices[-1] + 1])
        val_price = float(bin_edges[va_indices[0]])

        candidates = []

        vpoc_side = 'support' if vpoc_price < current_price else 'resistance'
        candidates.append(SRCandidate(
            price=round(vpoc_price, 2),
            source='VP_VPOC',
            weight=1.3,
            side=vpoc_side,
            extra={
                'total_bins': num_bins,
                'vpoc_volume_pct': (float(vol_bins[vpoc_idx]) / total_volume) * 100,
            },
            level=SRLevel.INTERMEDIATE,
            source_type=SRSourceType.STRUCTURAL,
            timeframe="30m_vp",
        ))

        if vah_price > current_price:
            candidates.append(SRCandidate(
                price=round(vah_price, 2),
                source='VP_VAH',
                weight=1.0,
                side='resistance',
                level=SRLevel.INTERMEDIATE,
                source_type=SRSourceType.STRUCTURAL,
                timeframe="30m_vp",
            ))

        if val_price < current_price:
            candidates.append(SRCandidate(
                price=round(val_price, 2),
                source='VP_VAL',
                weight=1.0,
                side='support',
                level=SRLevel.INTERMEDIATE,
                source_type=SRSourceType.STRUCTURAL,
                timeframe="30m_vp",
            ))

        return candidates

    except Exception as e:
        logger.warning(f"Volume Profile calculation failed: {e}")
        return []
