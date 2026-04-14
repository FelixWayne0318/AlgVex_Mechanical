"""
Mechanical Strategy Service — reads feature snapshots and computes scores.

Provides current state, signal history, and score time series for the web UI.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import settings

# Add AlgVex root to path for importing agents
_algvex_path = settings.ALGVEX_PATH
if _algvex_path and _algvex_path not in sys.path:
    sys.path.insert(0, _algvex_path)


def _get_snapshots_dir() -> Path:
    algvex = Path(_algvex_path) if _algvex_path else Path(".")
    return algvex / "data" / "feature_snapshots"


def _get_mechanical_state_file() -> Path:
    algvex = Path(_algvex_path) if _algvex_path else Path(".")
    return algvex / "data" / "mechanical_state.json"


def _load_zone_config() -> Dict[str, Any]:
    """Load zone_entry config from base.yaml."""
    try:
        import yaml
        algvex = Path(_algvex_path) if _algvex_path else Path(".")
        with open(algvex / "configs" / "base.yaml") as f:
            cfg = yaml.safe_load(f)
        ant = cfg.get("anticipatory", {})
        return ant.get("zone_entry", {})
    except Exception:
        return {}


# Cache for latest snapshot (30s TTL)
_cache: Dict[str, Any] = {"data": None, "ts": 0}
_CACHE_TTL = 30


def _load_latest_snapshot() -> Optional[Dict[str, Any]]:
    """Load the most recent feature snapshot."""
    snap_dir = _get_snapshots_dir()
    if not snap_dir.exists():
        return None

    files = sorted(snap_dir.glob("snapshot_*.json"), key=os.path.getmtime, reverse=True)
    if not files:
        return None

    try:
        with open(files[0]) as f:
            return json.load(f)
    except Exception:
        return None


def _load_snapshots(hours: int = 24, limit: int = 200) -> List[Dict[str, Any]]:
    """Load snapshots within a time window."""
    snap_dir = _get_snapshots_dir()
    if not snap_dir.exists():
        return []

    cutoff = time.time() - hours * 3600
    files = sorted(snap_dir.glob("snapshot_*.json"), key=os.path.getmtime, reverse=True)

    results = []
    for f in files[:limit]:
        if os.path.getmtime(str(f)) < cutoff:
            break
        try:
            with open(f) as fp:
                snap = json.load(fp)
            snap["_filename"] = f.name
            snap["_mtime"] = os.path.getmtime(str(f))
            results.append(snap)
        except Exception:
            continue

    return list(reversed(results))  # chronological order


def _extract_zone_conditions(features: Dict, config: Dict) -> Dict[str, bool]:
    """Check which zone conditions are met (for display)."""
    rsi_oversold_30m = config.get("rsi_oversold_30m", 50)
    rsi_oversold_4h = config.get("rsi_oversold_4h", 45)
    sr_proximity = config.get("support_proximity_atr", 3.0)

    def sf(key, default=0):
        v = features.get(key)
        if v is None:
            return default
        try:
            return float(v)
        except (ValueError, TypeError):
            return default

    ext_4h = str(features.get("extension_regime_4h", "NORMAL")).upper()
    ext_levels = {"EXTENDED": 1, "OVEREXTENDED": 2, "EXTREME": 3}

    rsi_30m = sf("rsi_30m", 50)
    rsi_4h = sf("rsi_4h", 50)
    cvd_30m = str(features.get("cvd_price_cross_30m", "")).upper()
    cvd_4h = str(features.get("cvd_price_cross_4h", "")).upper()
    sup_dist = sf("nearest_support_dist_atr", 99)
    sup_str = str(features.get("nearest_support_strength", "NONE")).upper()

    return {
        "extension_4h": ext_levels.get(ext_4h, 0) >= 1,
        "extension_4h_regime": ext_4h,
        "rsi_oversold": rsi_30m < rsi_oversold_30m or rsi_4h < rsi_oversold_4h,
        "rsi_30m": round(rsi_30m, 1),
        "rsi_4h": round(rsi_4h, 1),
        "cvd_accumulation": cvd_30m in ("ACCUMULATION", "ABSORPTION_BUY") or cvd_4h in ("ACCUMULATION", "ABSORPTION_BUY"),
        "cvd_30m": cvd_30m,
        "cvd_4h": cvd_4h,
        "sr_proximity": sup_dist < sr_proximity and sup_str in ("HIGH", "MEDIUM", "LOW"),
        "sr_distance_atr": round(sup_dist, 2),
        "sr_strength": sup_str,
    }


def get_mechanical_state() -> Dict[str, Any]:
    """Get current mechanical decision state."""
    now = time.time()
    if _cache["data"] and (now - _cache["ts"]) < _CACHE_TTL:
        return _cache["data"]

    snap = _load_latest_snapshot()
    if not snap:
        return {"status": "no_data", "message": "No feature snapshots available"}

    scores = snap.get("scores", {})
    features = snap.get("features", {})
    zone_cfg = _load_zone_config()

    # Direction lock state
    direction_lock = {"LONG": 0, "SHORT": 0}
    state_file = _get_mechanical_state_file()
    if state_file.exists():
        try:
            with open(state_file) as f:
                state = json.load(f)
            direction_lock = state.get("direction_sl_count", direction_lock)
        except Exception:
            pass

    # Thresholds
    thresholds = {
        "high": zone_cfg.get("high_threshold", 0.45),
        "med": zone_cfg.get("med_threshold", 0.35),
        "low": zone_cfg.get("low_threshold", 0.20),
    }

    net_raw = scores.get("anticipatory_raw", 0)
    abs_raw = abs(net_raw)
    zones = _extract_zone_conditions(features, zone_cfg)
    n_zones = sum(1 for k in ["extension_4h", "rsi_oversold", "cvd_accumulation", "sr_proximity"] if zones.get(k))

    # Determine signal tier
    if abs_raw >= thresholds["high"]:
        signal_tier = "HIGH"
    elif abs_raw >= thresholds["med"]:
        signal_tier = "MEDIUM"
    elif abs_raw >= thresholds["low"] and n_zones >= 1:
        signal_tier = "LOW"
    else:
        signal_tier = "HOLD"

    signal = "LONG" if net_raw > 0 else "SHORT" if net_raw < 0 else "HOLD"
    if signal_tier == "HOLD":
        signal = "HOLD"

    result = {
        "status": "active",
        "timestamp": snap.get("timestamp", ""),
        "price": features.get("price", 0),
        "net_raw": round(net_raw, 4),
        "signal": signal,
        "signal_tier": signal_tier,
        "structure": scores.get("structure", {}),
        "divergence": scores.get("divergence", {}),
        "order_flow": scores.get("order_flow", {}),
        "regime": scores.get("regime", "DEFAULT"),
        "trend_context": scores.get("trend_context", "NEUTRAL"),
        "zone_conditions": zones,
        "zone_count": n_zones,
        "direction_lock": direction_lock,
        "thresholds": thresholds,
    }

    _cache["data"] = result
    _cache["ts"] = now
    return result


def get_signal_history(limit: int = 50) -> List[Dict[str, Any]]:
    """Get historical signals from feature snapshots."""
    snapshots = _load_snapshots(hours=72, limit=limit * 2)
    zone_cfg = _load_zone_config()
    thresholds = {
        "high": zone_cfg.get("high_threshold", 0.45),
        "med": zone_cfg.get("med_threshold", 0.35),
        "low": zone_cfg.get("low_threshold", 0.20),
    }

    history = []
    for snap in snapshots:
        scores = snap.get("scores", {})
        features = snap.get("features", {})
        net_raw = scores.get("anticipatory_raw")
        if net_raw is None:
            continue

        abs_raw = abs(net_raw)
        zones = _extract_zone_conditions(features, zone_cfg)
        n_zones = sum(1 for k in ["extension_4h", "rsi_oversold", "cvd_accumulation", "sr_proximity"] if zones.get(k))

        if abs_raw >= thresholds["high"]:
            tier = "HIGH"
        elif abs_raw >= thresholds["med"]:
            tier = "MEDIUM"
        elif abs_raw >= thresholds["low"] and n_zones >= 1:
            tier = "LOW"
        else:
            tier = "HOLD"

        signal = ("LONG" if net_raw > 0 else "SHORT") if tier != "HOLD" else "HOLD"

        history.append({
            "timestamp": snap.get("timestamp", ""),
            "price": features.get("price", 0),
            "net_raw": round(net_raw, 4),
            "signal": signal,
            "tier": tier,
            "structure_dir": scores.get("structure", {}).get("direction", "N/A"),
            "structure_score": scores.get("structure", {}).get("score", 0),
            "divergence_dir": scores.get("divergence", {}).get("direction", "N/A"),
            "divergence_score": scores.get("divergence", {}).get("score", 0),
            "order_flow_dir": scores.get("order_flow", {}).get("direction", "N/A"),
            "order_flow_score": scores.get("order_flow", {}).get("score", 0),
            "regime": scores.get("regime", "DEFAULT"),
            "zone_count": n_zones,
        })

    return history[-limit:]


def get_score_timeseries(hours: int = 24) -> List[Dict[str, Any]]:
    """Get net_raw time series for charting."""
    snapshots = _load_snapshots(hours=hours)

    series = []
    for snap in snapshots:
        scores = snap.get("scores", {})
        net_raw = scores.get("anticipatory_raw")
        if net_raw is None:
            continue

        series.append({
            "timestamp": snap.get("timestamp", ""),
            "net_raw": round(net_raw, 4),
            "structure": scores.get("structure", {}).get("score", 0),
            "divergence": scores.get("divergence", {}).get("score", 0),
            "order_flow": scores.get("order_flow", {}).get("score", 0),
            "price": snap.get("features", {}).get("price", 0),
        })

    return series
