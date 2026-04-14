"""
Fractional Kelly Position Sizing.

Replaces fixed confidence_mapping (80/50/30%) with data-driven Kelly criterion.
Blends with fixed mapping during warm-up period (< min_trades trades).

Kelly fraction: f* = (p × b - q) / b
Where: p = win_rate, q = 1-p, b = avg_win_rr / avg_loss_rr

Reference: docs/upgrade_plan_v2/07_POSITION_RISK.md

Integration: called from strategy/trading_logic.py calculate_position_size()
when kelly.enabled=true in configs/base.yaml.
"""

import json
import logging
import math
from collections import defaultdict
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_MEMORY_FILE = _PROJECT_ROOT / "data" / "trading_memory.json"


class KellySizer:
    """Fractional Kelly × Regime × Drawdown position sizing.

    Three layers:
      Layer 1: Kelly optimal × fraction (or blended with fixed during warm-up)
      Layer 2: × regime multiplier (from HMM or ADX-based)
      Layer 3: × drawdown scaling (reduce as DD increases)
    """

    # Regime multipliers
    _REGIME_MULT = {
        "TRENDING_UP": 1.2,
        "TRENDING_DOWN": 0.6,
        "RANGING": 0.8,
        "HIGH_VOLATILITY": 0.3,
        # v44 backward compatible names
        "STRONG_TREND": 1.2,
        "WEAK_TREND": 0.9,
    }

    # Fixed mapping fallback (matches v44.0 confidence_mapping)
    _FIXED_MAPPING = {"HIGH": 80, "MEDIUM": 50, "LOW": 30}

    def __init__(self, config: Optional[Dict] = None):
        cfg = (config or {}).get("kelly", {})
        self._fraction = cfg.get("fraction", 0.25)
        self._min_trades = cfg.get("min_trades_for_kelly", 50)
        self._blend_full_at = cfg.get("kelly_blend_full_at", 100)
        self._min_pct = cfg.get("min_position_pct", 5)
        self._max_pct = cfg.get("max_position_pct", 100)
        self._enabled = cfg.get("enabled", False)

        # Cache per-confidence stats
        self._stats: Optional[Dict[str, Dict]] = None
        self._trade_count = 0

    def calculate(
        self,
        confidence: str,
        regime: str = "RANGING",
        current_dd_pct: float = 0.0,
        dd_threshold_pct: float = 15.0,
    ) -> Tuple[float, Dict[str, Any]]:
        """Calculate position size percentage.

        Parameters
        ----------
        confidence : str
            HIGH / MEDIUM / LOW
        regime : str
            HMM or ADX regime label
        current_dd_pct : float
            Current drawdown percentage (0-100, positive = in drawdown)
        dd_threshold_pct : float
            Drawdown halt threshold percentage

        Returns
        -------
        Tuple[float, Dict]:
            (size_pct in [min_pct, max_pct], calculation_details)
        """
        confidence = confidence.upper()
        details = {"method": "kelly" if self._enabled else "fixed"}

        if not self._enabled:
            pct = self._FIXED_MAPPING.get(confidence, 50)
            details["source"] = "fixed_mapping"
            details["size_pct"] = pct
            return float(pct), details

        # Load stats if needed
        if self._stats is None:
            self._load_stats()

        # Layer 1: Kelly or blended
        stats = self._stats.get(confidence, {})
        trade_count = stats.get("count", 0)
        total_trades = self._trade_count

        if total_trades < self._min_trades:
            # Warm-up: use fixed mapping
            kelly_pct = self._FIXED_MAPPING.get(confidence, 50)
            kelly_weight = 0.0
            details["source"] = f"fixed_warmup ({total_trades}/{self._min_trades} trades)"
        else:
            win_rate = stats.get("win_rate", 0.5)
            avg_win_rr = stats.get("avg_win_rr", 1.5)
            avg_loss_rr = stats.get("avg_loss_rr", 1.0)

            # Kelly formula: f* = (p × b - q) / b
            # where b = avg_win / avg_loss
            if avg_loss_rr > 0:
                b = avg_win_rr / avg_loss_rr
            else:
                b = avg_win_rr if avg_win_rr > 0 else 1.0

            q = 1.0 - win_rate
            kelly_raw = (win_rate * b - q) / b if b > 0 else 0.0
            kelly_raw = max(0.0, kelly_raw)  # Negative Kelly = don't trade

            # Apply fraction
            kelly_pct = kelly_raw * self._fraction * 100  # Convert to percentage

            # Blend weight: linear from min_trades → blend_full_at
            if total_trades >= self._blend_full_at:
                kelly_weight = 1.0
            else:
                kelly_weight = (total_trades - self._min_trades) / max(1, self._blend_full_at - self._min_trades)
                kelly_weight = max(0.0, min(1.0, kelly_weight))

            details["kelly_raw"] = round(kelly_raw, 4)
            details["kelly_pct"] = round(kelly_pct, 2)
            details["kelly_weight"] = round(kelly_weight, 2)
            details["win_rate"] = round(win_rate, 4)
            details["avg_win_rr"] = round(avg_win_rr, 2)
            details["avg_loss_rr"] = round(avg_loss_rr, 2)
            details["b_ratio"] = round(b, 2)
            details["source"] = "kelly"

        # Blend Kelly with fixed
        fixed_pct = self._FIXED_MAPPING.get(confidence, 50)
        blended = kelly_pct * kelly_weight + fixed_pct * (1 - kelly_weight) if self._enabled else fixed_pct

        # Layer 2: Regime multiplier
        regime_mult = self._REGIME_MULT.get(regime, 0.8)
        sized = blended * regime_mult
        details["regime"] = regime
        details["regime_mult"] = regime_mult

        # Layer 3: Drawdown scaling
        if dd_threshold_pct > 0 and current_dd_pct > 0:
            dd_scale = max(0.2, 1.0 - current_dd_pct / dd_threshold_pct)
        else:
            dd_scale = 1.0
        sized *= dd_scale
        details["dd_scale"] = round(dd_scale, 2)

        # Clamp
        final = max(self._min_pct, min(self._max_pct, sized))
        details["final_pct"] = round(final, 2)
        details["trade_count"] = total_trades

        return round(final, 2), details

    def _load_stats(self) -> None:
        """Load per-confidence win_rate and avg_rr from trading_memory.json."""
        self._stats = {}
        self._trade_count = 0

        if not _MEMORY_FILE.exists():
            logger.info("No trading_memory.json — Kelly using fixed mapping")
            return

        try:
            with open(_MEMORY_FILE) as f:
                memories = json.load(f)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"Failed to load trading memory for Kelly: {e}")
            return

        # Group by confidence
        buckets: Dict[str, list] = defaultdict(list)
        for m in memories:
            ev = m.get("evaluation", {})
            if not ev or "direction_correct" not in ev:
                continue
            conf = ev.get("confidence", "MEDIUM").upper()
            pnl = m.get("pnl")
            rr = ev.get("actual_rr", 0)
            if pnl is None:
                # Compute from entry/exit
                entry = ev.get("entry_price", 0)
                exit_p = ev.get("exit_price", 0)
                if entry and entry > 0 and exit_p:
                    decision = m.get("decision", "LONG")
                    if decision in ("LONG", "BUY"):
                        pnl = (exit_p - entry) / entry * 100
                    else:
                        pnl = (entry - exit_p) / entry * 100

            if pnl is not None:
                buckets[conf].append({
                    "won": pnl > 0,
                    "rr": abs(rr) if rr else abs(pnl) / 100,
                    "pnl": pnl,
                })
            self._trade_count += 1

        for conf, trades in buckets.items():
            n = len(trades)
            wins = [t for t in trades if t["won"]]
            losses = [t for t in trades if not t["won"]]
            self._stats[conf] = {
                "count": n,
                "win_rate": len(wins) / n if n > 0 else 0.5,
                "avg_win_rr": sum(t["rr"] for t in wins) / len(wins) if wins else 1.5,
                "avg_loss_rr": sum(t["rr"] for t in losses) / len(losses) if losses else 1.0,
            }

        logger.info(
            f"Kelly stats loaded: {self._trade_count} trades, "
            f"tiers={list(self._stats.keys())}"
        )

    def refresh_stats(self) -> None:
        """Force reload stats from trading_memory.json."""
        self._stats = None
        self._load_stats()
