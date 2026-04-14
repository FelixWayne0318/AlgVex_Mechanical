"""
Hold Probability 校准数据加载器 v1.1

职责:
- 从 data/calibration/latest.json 加载校准因子
- 进程内缓存 (按文件 mtime 刷新)
- 提供 v8.2 硬编码默认值作为兜底
- 提供校准状态查询 (age, source)

数据流:
  calibrate_hold_probability.py --auto-calibrate  (每周 cron)
    ↓ 写入
  data/calibration/latest.json
    ↓ 读取
  sr_zone_calculator._estimate_hold_probability()

Author: AlgVex Team
Date: 2026-02
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

# File paths
_PROJECT_ROOT = Path(__file__).parent.parent
CALIBRATION_DIR = _PROJECT_ROOT / 'data' / 'calibration'
CALIBRATION_FILE = CALIBRATION_DIR / 'latest.json'

# In-memory cache (v2.0 Phase 1: thread-safe via _cache_lock)
import threading
_cache_lock = threading.Lock()
_cached_calibration: Optional[Dict[str, Any]] = None
_cache_file_mtime: float = 0.0
_is_from_file: bool = False

# Minimum samples required to trust calibration data
MIN_SAMPLES = 100

# ============================================================================
# v8.2 hardcoded defaults (used when no calibration file exists)
# Derived from 30-day backtest, 2551 samples, 2026-02-17
# ============================================================================
DEFAULT_CALIBRATION: Dict[str, Any] = {
    'version': 'v8.2-default',
    'sample_count': 0,
    'overall_hold_rate': 0.634,

    # Base formula: hold = intercept + slope * (weight / max_weight)
    # Range: [0.58, 0.72]
    'base_intercept': 0.58,
    'base_slope': 0.14,

    # Source type multipliers (relative to overall hold rate)
    # STRUCTURAL=63.2%, PROJECTED=67.0%, TECHNICAL=56.1%
    'source_factors': {
        'STRUCTURAL': 1.000,
        'ORDER_FLOW': 1.030,
        'PROJECTED': 1.060,
        'PSYCHOLOGICAL': 0.950,
        'TECHNICAL': 0.890,
    },

    # Touch count multipliers
    # 4=66.1%, 2-3=64.7%, 0-1=62.7%, 5+=62.3%
    'touch_factors': {
        '0-1': 0.990,
        '2-3': 1.020,
        '4': 1.040,
        '5+': 0.980,
    },

    # Swing point multipliers
    # with=64.0%, without=62.0%
    'swing_factor_with': 1.010,
    'swing_factor_without': 1.000,

    # Side multipliers (support vs resistance)
    # Empty = no side adjustment (default, market-regime dependent)
    'side_factors': {},
}


def load_calibration(force_reload: bool = False) -> Dict[str, Any]:
    """
    Load calibration factors from data/calibration/latest.json.

    Uses in-memory cache with mtime-based invalidation.
    Falls back to DEFAULT_CALIBRATION if file missing or invalid.

    Parameters
    ----------
    force_reload : bool
        Force re-read from disk (ignores cache).

    Returns
    -------
    Dict with calibration factors.
    """
    global _cached_calibration, _cache_file_mtime, _is_from_file

    with _cache_lock:
        return _load_calibration_locked(force_reload)


def _load_calibration_locked(force_reload: bool) -> Dict[str, Any]:
    """Internal: load calibration while holding _cache_lock."""
    global _cached_calibration, _cache_file_mtime, _is_from_file

    # Return cache if valid
    if not force_reload and _cached_calibration is not None:
        try:
            current_mtime = CALIBRATION_FILE.stat().st_mtime
            if current_mtime == _cache_file_mtime:
                return _cached_calibration
        except OSError:
            return _cached_calibration

    # Try loading from file
    try:
        if CALIBRATION_FILE.exists():
            with open(CALIBRATION_FILE) as f:
                data = json.load(f)

            # Validate minimum fields
            if (
                data.get('sample_count', 0) >= MIN_SAMPLES
                and 'base_intercept' in data
                and 'base_slope' in data
                and 'source_factors' in data
            ):
                _cached_calibration = data
                _cache_file_mtime = CALIBRATION_FILE.stat().st_mtime
                _is_from_file = True
                logger.info(
                    f"Loaded calibration: {data.get('version', '?')}, "
                    f"{data['sample_count']} samples, "
                    f"calibrated {data.get('calibrated_at', '?')}"
                )

                # v18.0: Check calibration staleness — inline age calculation
                # NOTE: Do NOT call get_calibration_age_hours() here — it calls
                # load_calibration() which would create indirect recursion.
                # datetime and timezone are already imported at module level (L23).
                calibrated_at = data.get('calibrated_at')
                if calibrated_at:
                    try:
                        cal_time = datetime.fromisoformat(
                            calibrated_at.replace('Z', '+00:00')
                        )
                        age_hours = (
                            datetime.now(timezone.utc) - cal_time
                        ).total_seconds() / 3600.0
                        if age_hours > 7 * 24:  # > 7 days
                            logger.warning(
                                f"⚠️ S/R calibration is {age_hours/24:.1f} days old "
                                f"(>7 days). Falling back to defaults. "
                                f"Run calibrate_hold_probability.py to refresh."
                            )
                            _cached_calibration = DEFAULT_CALIBRATION.copy()
                            _is_from_file = False
                            return _cached_calibration
                    except (ValueError, TypeError):
                        pass  # Unparseable timestamp — use calibration as-is

                return data
            else:
                logger.warning(
                    f"Calibration file invalid or insufficient samples "
                    f"({data.get('sample_count', 0)} < {MIN_SAMPLES}), "
                    f"using defaults"
                )
    except (json.JSONDecodeError, OSError, KeyError) as e:
        logger.warning(f"Failed to load calibration file: {e}, using defaults")

    # Return defaults
    _cached_calibration = DEFAULT_CALIBRATION.copy()
    _is_from_file = False
    return _cached_calibration


def get_source_factor(source_type: str, calibration: Optional[Dict] = None) -> float:
    """Get calibrated source type multiplier."""
    cal = calibration or load_calibration()
    return cal.get('source_factors', {}).get(source_type, 1.0)


def get_touch_factor(touch_count: int, calibration: Optional[Dict] = None) -> float:
    """Get calibrated touch count multiplier."""
    cal = calibration or load_calibration()
    factors = cal.get('touch_factors', {})

    if touch_count == 4:
        return factors.get('4', 1.04)
    elif touch_count in (2, 3):
        return factors.get('2-3', 1.02)
    elif touch_count >= 5:
        return factors.get('5+', 0.98)
    else:
        return factors.get('0-1', 0.99)


def get_side_factor(side: str, calibration: Optional[Dict] = None) -> float:
    """
    Get calibrated side (support/resistance) multiplier.

    Returns 1.0 if no side calibration data (default behavior).
    Side factors are market-regime dependent and only populated
    by auto-calibration.
    """
    cal = calibration or load_calibration()
    return cal.get('side_factors', {}).get(side, 1.0)


def get_swing_factor(has_swing: bool, calibration: Optional[Dict] = None) -> float:
    """
    Get calibrated swing point multiplier.

    Parameters
    ----------
    has_swing : bool
        Whether the zone has a swing point.
    """
    cal = calibration or load_calibration()
    if has_swing:
        return cal.get('swing_factor_with', 1.01)
    return cal.get('swing_factor_without', 1.0)


def get_base_formula(calibration: Optional[Dict] = None) -> Tuple[float, float]:
    """
    Get calibrated base formula parameters.

    Returns (intercept, slope) for: hold = intercept + slope * (weight / max_weight)
    """
    cal = calibration or load_calibration()
    return (
        cal.get('base_intercept', 0.58),
        cal.get('base_slope', 0.14),
    )


def is_using_defaults() -> bool:
    """
    Check if calibration is using hardcoded defaults (no file loaded).

    Useful for heartbeat/status reporting.
    """
    load_calibration()  # Ensure cache is populated
    return not _is_from_file


def get_calibration_age_hours() -> Optional[float]:
    """
    Get age of current calibration data in hours.

    Returns None if using defaults (no calibrated_at timestamp).
    Useful for staleness monitoring — heartbeat warns when using defaults.
    """
    cal = load_calibration()
    calibrated_at = cal.get('calibrated_at')
    if not calibrated_at:
        return None

    try:
        if isinstance(calibrated_at, str):
            # Parse ISO format
            cal_time = datetime.fromisoformat(calibrated_at.replace('Z', '+00:00'))
        else:
            return None

        now = datetime.now(timezone.utc)
        delta = now - cal_time
        return delta.total_seconds() / 3600.0
    except (ValueError, TypeError):
        return None


def get_calibration_summary() -> Dict[str, Any]:
    """
    Get a compact summary of current calibration state.

    Used by heartbeat and diagnostics.
    """
    cal = load_calibration()
    age = get_calibration_age_hours()

    return {
        'version': cal.get('version', '?'),
        'source': 'file' if _is_from_file else 'defaults',
        'sample_count': cal.get('sample_count', 0),
        'overall_hold_rate': cal.get('overall_hold_rate', 0),
        'age_hours': round(age, 1) if age is not None else None,
        'stale': not _is_from_file,  # defaults = stale or missing
    }
