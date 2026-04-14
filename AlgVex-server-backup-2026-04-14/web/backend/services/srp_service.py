"""
SRP Strategy Service v5.0 — All backtest calculations via NautilusTrader BacktestEngine.

Data flow:
  get_parameters()     → configs/base.yaml → srp: section
  get_state()          → data/srp_state.json (written by NT SRPStrategy)
  get_service_status() → systemctl is-active nautilus-srp
  run_backtest()       → subprocess: backtest_srp_engine.py (NT BacktestEngine)
  run_parity_check()   → subprocess: pine_tv_comparator.py
  get_*_result()       → data/*.json (read cached results)
"""
import json
import math
import yaml
import subprocess
import shutil
from pathlib import Path
from typing import Dict, Any
from datetime import datetime


def _sanitize_nan(obj):
    """Recursively replace NaN/inf with None for JSON compliance."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_nan(v) for v in obj]
    return obj

from core.config import settings

ALGVEX_ROOT = Path(settings.ALGVEX_PATH)


class SRPService:
    """SRP strategy service — reads NT state, delegates computation to NT BacktestEngine."""

    def __init__(self):
        self.algvex = ALGVEX_ROOT
        self.data = self.algvex / "data"
        self.configs = self.algvex / "configs"
        self._python = str(self.algvex / "venv" / "bin" / "python3")
        if not Path(self._python).exists():
            self._python = shutil.which("python3") or "python3"
        self._running = False  # Concurrency guard

    # =========================================================================
    # Parameters (from configs/base.yaml)
    # =========================================================================

    def get_parameters(self) -> Dict[str, Any]:
        try:
            cfg_file = self.configs / "base.yaml"
            if not cfg_file.exists():
                return {"error": "Config file not found"}
            with open(cfg_file) as f:
                config = yaml.safe_load(f)
            srp = config.get("srp", {})
            return {
                "srp": {
                    "srp_pct": srp.get("srp_pct", 1.0),
                    "vwma_length": srp.get("vwma_length", 14),
                    "rsi_mfi_below": srp.get("rsi_mfi_below", 55.0),
                    "rsi_mfi_above": srp.get("rsi_mfi_above", 100.0),
                    "rsi_mfi_period": srp.get("rsi_mfi_period", 7),
                    "sizing_mode": srp.get("sizing_mode", "percent"),
                    "base_order_pct": srp.get("base_order_pct", 10.0),
                    "dca_multiplier": srp.get("dca_multiplier", 1.5),
                    "max_dca_count": srp.get("max_dca_count", 4),
                    "dca_min_change_pct": srp.get("dca_min_change_pct", 3.0),
                    "dca_type": srp.get("dca_type", "volume_multiply"),
                    "mintp": srp.get("mintp", 0.025),
                    "max_portfolio_loss_pct": srp.get("max_portfolio_loss_pct", 0.06),
                    "timeframe": srp.get("timeframe", "30m"),
                    "short_enabled": False,
                },
            }
        except Exception as e:
            return {"error": str(e)}

    # =========================================================================
    # Position State (from NT's srp_state.json)
    # =========================================================================

    def get_state(self) -> Dict[str, Any]:
        state_file = self.data / "srp_state.json"
        if not state_file.exists():
            return {"has_position": False, "side": None, "dca_count": 0,
                    "avg_price": 0, "total_quantity": 0, "dealcount": 0}
        try:
            with open(state_file) as f:
                s = json.load(f)
            v_avg = s.get("v_avg", 0)
            mintp = 0.025
            tp_target = v_avg * (1 + mintp) if v_avg > 0 else 0
            return {
                "has_position": s.get("side") is not None,
                "side": s.get("side"),
                "dca_count": s.get("dca_count", 0),
                "avg_price": s.get("avg_price", 0),
                "total_quantity": s.get("total_quantity", 0),
                "total_cost": s.get("total_cost", 0),
                "deal_base": s.get("deal_base", 0),
                "dealcount": s.get("dealcount", 0),
                "socounter": s.get("socounter", 0),
                "v_avg": v_avg,
                "v_count": s.get("v_count", 0),
                "v_last_px": s.get("v_last_px", 0),
                "tp_target": round(tp_target, 2),
                "dca_entries": s.get("dca_entries", []),
                "saved_at": s.get("saved_at"),
            }
        except Exception as e:
            return {"error": str(e)}

    # =========================================================================
    # Service Status (systemd)
    # =========================================================================

    def get_service_status(self) -> Dict[str, Any]:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "nautilus-srp"],
                capture_output=True, text=True, timeout=5,
            )
            status = result.stdout.strip()
            return {"status": status, "running": status == "active"}
        except Exception as e:
            return {"status": "unknown", "running": False, "error": str(e)}

    # =========================================================================
    # Backtest — delegates to NT BacktestEngine
    # =========================================================================

    def run_backtest(
        self,
        days: int = 456,
        balance: float = 1500,
        srp_pct: float | None = None,
        dca_spacing: float | None = None,
        dca_mult: float | None = None,
        max_dca_count: int | None = None,
        tp_pct: float | None = None,
        sl_pct: float | None = None,
        include_equity_curve: bool = False,
        include_trades: bool = False,
    ) -> Dict[str, Any]:
        if self._running:
            return {"error": "A backtest is already running"}
        self._running = True
        try:
            output_file = self.data / "srp_backtest_result.json"
            cmd = [
                self._python, str(self.algvex / "scripts" / "backtest_srp_engine.py"),
                "--days", str(days), "--balance", str(balance),
                "--json", "--output", str(output_file),
            ]
            if srp_pct is not None:
                cmd += ["--srp-pct", str(srp_pct)]
            if dca_spacing is not None:
                cmd += ["--dca-spacing", str(dca_spacing)]
            if dca_mult is not None:
                cmd += ["--dca-mult", str(dca_mult)]
            if max_dca_count is not None:
                cmd += ["--max-dca-count", str(max_dca_count)]
            if tp_pct is not None:
                cmd += ["--tp-pct", str(tp_pct)]
            if sl_pct is not None:
                cmd += ["--sl-pct", str(sl_pct)]
            if include_equity_curve:
                cmd.append("--include-equity-curve")
            if include_trades:
                cmd.append("--include-trades")

            result = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=300,
                cwd=str(self.algvex),
            )
            if output_file.exists():
                with open(output_file) as f:
                    return _sanitize_nan(json.load(f))
            return {
                "error": "Backtest completed but no output",
                "stderr": result.stderr[-500:] if result.stderr else "",
            }
        except subprocess.TimeoutExpired:
            return {"error": "Backtest timed out (5 min)"}
        except Exception as e:
            return {"error": str(e)}
        finally:
            self._running = False

    def get_backtest_result(self) -> Dict[str, Any]:
        result_file = self.data / "srp_backtest_result.json"
        if not result_file.exists():
            return {"error": "No backtest results. Run backtest first."}
        try:
            with open(result_file) as f:
                data = json.load(f)
            data["file_date"] = datetime.fromtimestamp(
                result_file.stat().st_mtime
            ).isoformat()
            return data
        except Exception as e:
            return {"error": str(e)}

    # =========================================================================
    # Parity Check — Pine Simulator vs Python Backtest
    # =========================================================================

    def run_parity_check(self, days: int = 456) -> Dict[str, Any]:
        if self._running:
            return {"error": "A computation is already running"}
        self._running = True
        try:
            result = subprocess.run(
                [self._python, str(self.algvex / "scripts" / "pine_tv_comparator.py"),
                 "--days", str(days)],
                capture_output=True, text=True, timeout=120,
                cwd=str(self.algvex),
            )
            output = result.stdout
            parity = "PERFECT PARITY" in output
            return {
                "parity": parity,
                "verdict": "PERFECT PARITY" if parity else "DIFFERENCES FOUND",
                "days": days,
                "output": output[-2000:] if output else "",
            }
        except subprocess.TimeoutExpired:
            return {"error": "Parity check timed out (2 min)"}
        except Exception as e:
            return {"error": str(e)}
        finally:
            self._running = False

    # =========================================================================
    # Walk-Forward (read-only — CLI runs, web displays)
    # =========================================================================

    def get_walkforward_result(self) -> Dict[str, Any]:
        result_file = self.data / "srp_walkforward_result.json"
        if not result_file.exists():
            return {
                "error": "No walk-forward results.",
                "hint": "Run: python3 scripts/walk_forward_srp.py --days 456 --capital 1500",
            }
        try:
            with open(result_file) as f:
                data = json.load(f)
            data["file_date"] = datetime.fromtimestamp(
                result_file.stat().st_mtime
            ).isoformat()
            return data
        except Exception as e:
            return {"error": str(e)}


# Singleton
_service = None
def get_srp_service() -> SRPService:
    global _service
    if _service is None:
        _service = SRPService()
    return _service
