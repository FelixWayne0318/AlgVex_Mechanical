"""
Multi-Agent Trading Analyzer — Mechanical Mode

Phase 3 cleanup: all AI components (Bull/Bear/Judge/Entry Timing/Risk Manager,
memory system, quality auditor) removed. Only the mechanical anticipatory path
is retained.

v10.0: Initial mechanical-only version per PLAN_MECHANICAL_TRADING.md
"""

import json
import logging
import os
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

# S/R Zone Calculator (multi-source support/resistance detection)
from utils.sr_zone_calculator import SRZoneCalculator
from agents.report_formatter import ReportFormatterMixin


class MultiAgentAnalyzer(ReportFormatterMixin):
    """
    Multi-agent trading analyzer (mechanical mode only).

    Uses compute_anticipatory_scores() + mechanical_decide() to produce
    signals without any AI API calls.
    """

    def __init__(
        self,
        sr_zones_config: Optional[Dict] = None,
    ):
        # Setup logger
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

        # Used by record_outcome() and Web API
        self.call_trace: List[Dict[str, Any]] = []
        self.decision_memory: List[Dict[str, Any]] = []

        # v40.0: TRANSITIONING regime hysteresis state (cross-restart persistence)
        self._prev_regime_transition: str = "NONE"
        self._load_hysteresis_state()

        # v3.8: S/R Zone Calculator (multi-source support/resistance)
        sr_cfg = sr_zones_config or {}
        swing_cfg = sr_cfg.get('swing_detection', {})
        cluster_cfg = sr_cfg.get('clustering', {})
        scoring_cfg = sr_cfg.get('scoring', {})
        hard_ctrl_cfg = sr_cfg.get('hard_control', {})
        aggr_cfg = sr_cfg.get('aggregation', {})
        round_cfg = sr_cfg.get('round_number', {})

        self.sr_calculator = SRZoneCalculator(
            cluster_pct=cluster_cfg.get('cluster_pct', 0.5),
            zone_expand_pct=sr_cfg.get('zone_expand_pct', 0.1),
            hard_control_threshold_pct=hard_ctrl_cfg.get('threshold_pct', 1.0),
            hard_control_threshold_mode=hard_ctrl_cfg.get('threshold_mode', 'fixed'),
            hard_control_atr_multiplier=hard_ctrl_cfg.get('atr_multiplier', 0.5),
            hard_control_atr_min_pct=hard_ctrl_cfg.get('atr_min_pct', 0.3),
            hard_control_atr_max_pct=hard_ctrl_cfg.get('atr_max_pct', 2.0),
            swing_detection_enabled=swing_cfg.get('enabled', True),
            swing_left_bars=swing_cfg.get('left_bars', 5),
            swing_right_bars=swing_cfg.get('right_bars', 5),
            swing_weight=swing_cfg.get('weight', 1.2),
            swing_max_age=swing_cfg.get('max_swing_age', 100),
            use_atr_adaptive=cluster_cfg.get('use_atr_adaptive', True),
            atr_cluster_multiplier=cluster_cfg.get('atr_cluster_multiplier', 0.5),
            touch_count_enabled=scoring_cfg.get('touch_count_enabled', True),
            touch_threshold_atr=scoring_cfg.get('touch_threshold_atr', 0.3),
            optimal_touches=tuple(scoring_cfg.get('optimal_touches', [2, 3])),
            decay_after_touches=scoring_cfg.get('decay_after_touches', 4),
            same_data_weight_cap=aggr_cfg.get('same_data_weight_cap', 2.5),
            max_zone_weight=aggr_cfg.get('max_zone_weight', 6.0),
            confluence_bonus_2=aggr_cfg.get('confluence_bonus_2_sources', 0.2),
            confluence_bonus_3=aggr_cfg.get('confluence_bonus_3_sources', 0.5),
            round_number_btc_step=round_cfg.get('btc_step', 5000),
            round_number_count=round_cfg.get('count', 3),
            logger=self.logger,
        )

        # Cache for S/R zones (populated during mechanical_analyze)
        self._sr_zones_cache: Optional[Dict[str, Any]] = None
        self._alignment_data: Optional[Dict[str, Any]] = None

    # ── v40.0: Hysteresis state persistence ──

    def _load_hysteresis_state(self) -> None:
        """Load TRANSITIONING hysteresis state from disk (cross-restart)."""
        path = os.path.join("data", "hysteresis_state.json")
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    state = json.load(f)
                self._prev_regime_transition = state.get("prev_regime_transition", "NONE")
        except Exception:
            self._prev_regime_transition = "NONE"

    def _save_hysteresis_state(self) -> None:
        """Persist TRANSITIONING hysteresis state to disk."""
        path = os.path.join("data", "hysteresis_state.json")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump({"prev_regime_transition": self._prev_regime_transition}, f)
        except Exception:
            pass  # Non-critical — hysteresis resets gracefully on failure

    # ── Template lesson + memory recording (mechanical mode) ──

    @staticmethod
    def _generate_lesson(grade: str, pnl: float) -> str:
        """Generate template lesson for mechanical mode (replaces LLM reflection)."""
        if grade in ("A+", "A", "B"):
            return f"Profitable trade ({grade}), {pnl:+.2f}%. Anticipatory signal confirmed."
        elif grade == "D":
            return f"Disciplined loss ({grade}), {pnl:+.2f}%. SL held within plan."
        else:
            return f"Poor trade ({grade}), {pnl:+.2f}%. Review signal strength."

    def record_outcome(self, **kwargs) -> None:
        """
        Record mechanical trade outcome to trading_memory.json.

        Writes a compact entry with template lesson (no LLM calls).
        Max 500 entries, FIFO rotation.
        """
        import json as _json

        try:
            evaluation = kwargs.get("evaluation") or {}
            grade = evaluation.get("grade", "?")
            pnl = float(kwargs.get("pnl", 0.0))
            decision = kwargs.get("decision", "")
            conditions = kwargs.get("conditions", "")
            close_reason = kwargs.get("close_reason", "")

            lesson = self._generate_lesson(grade, pnl)

            entry = {
                "decision": decision,
                "pnl": round(pnl, 4),
                "conditions": conditions,
                "lesson": lesson,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "close_reason": close_reason,
                "winning_side": "BULL" if decision == "LONG" else "BEAR" if decision == "SHORT" else "TIE",
                "evaluation": evaluation,
                "source": "mechanical",
            }

            # Append to in-memory list (used by Web API)
            self.decision_memory.append(entry)

            # Persist to file (max 500 entries, FIFO)
            memory_path = os.path.join("data", "trading_memory.json")
            try:
                os.makedirs(os.path.dirname(memory_path), exist_ok=True)
                existing: List[Dict[str, Any]] = []
                if os.path.exists(memory_path):
                    with open(memory_path, "r") as f:
                        existing = _json.load(f)
                    if not isinstance(existing, list):
                        existing = []
                existing.append(entry)
                if len(existing) > 500:
                    existing = existing[-500:]
                with open(memory_path, "w") as f:
                    _json.dump(existing, f, ensure_ascii=False, indent=2)
                self.logger.info(
                    f"📝 Mechanical trade recorded: {decision} {grade} {pnl:+.2f}%"
                )
            except Exception as file_err:
                self.logger.error(f"Failed to write trading_memory.json: {file_err}")

        except Exception as e:
            self.logger.error(f"record_outcome failed: {e}", exc_info=True)

    # ── Core mechanical analysis ──

    def mechanical_analyze(
        self,
        technical_report: Dict[str, Any],
        sentiment_report: Optional[Dict[str, Any]] = None,
        current_position: Optional[Dict[str, Any]] = None,
        price_data: Optional[Dict[str, Any]] = None,
        order_flow_report: Optional[Dict[str, Any]] = None,
        derivatives_report: Optional[Dict[str, Any]] = None,
        binance_derivatives_report: Optional[Dict[str, Any]] = None,
        orderbook_report: Optional[Dict[str, Any]] = None,
        account_context: Optional[Dict[str, Any]] = None,
        order_flow_report_4h: Optional[Dict[str, Any]] = None,
        fear_greed_report: Optional[Dict[str, Any]] = None,
        sr_zones: Optional[Dict[str, Any]] = None,
        atr_value: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Mechanical anticipatory analysis — no AI API calls.

        Uses compute_anticipatory_scores() + mechanical_decide() to produce
        a signal dict 100% compatible with the downstream execution pipeline.

        v10.0: Initial implementation per PLAN_MECHANICAL_TRADING.md
        """
        from agents.mechanical_decide import mechanical_decide

        try:
            # Step 0: Calculate S/R zones if not provided
            if not sr_zones:
                try:
                    td = technical_report or {}
                    current_price = td.get('current_price', 0)
                    if not current_price:
                        current_price = (price_data or {}).get('price', 0)
                    if current_price > 0:
                        sr_zones = self._calculate_sr_zones(
                            current_price=current_price,
                            technical_data=td,
                            orderbook_data=orderbook_report,
                            atr_value=atr_value,
                        )
                        self._sr_zones_cache = sr_zones
                        self.logger.debug(f"S/R zones calculated: {len(sr_zones.get('support_zones', []))}S/{len(sr_zones.get('resistance_zones', []))}R")
                except Exception as e:
                    self.logger.debug(f"S/R zone calculation skipped: {e}")

            # Step 1: Extract features (same as AI path)
            feature_dict = self.extract_features(
                technical_data=technical_report,
                sentiment_data=sentiment_report,
                order_flow_data=order_flow_report,
                order_flow_4h=order_flow_report_4h,
                derivatives_data=derivatives_report,
                binance_derivatives=binance_derivatives_report,
                orderbook_data=orderbook_report,
                sr_zones=sr_zones,
                current_position=current_position,
                account_context=account_context,
                fear_greed_report=fear_greed_report,
            )

            # Step 2: Compute anticipatory scores (3 new dimensions: Structure/Divergence/Order Flow)
            # This is the REAL anticipatory engine — NOT a mapping from old trend/momentum scores.
            regime_config = {}
            try:
                from utils.config_manager import ConfigManager
                cm = ConfigManager()
                cm.load()
                regime_config = cm.get('anticipatory', 'regime_config') or {}
                # v48.0: Inject zone_entry config for mechanical_decide
                zone_cfg = cm.get('anticipatory', 'zone_entry') or {}
                if zone_cfg:
                    regime_config['_zone_entry'] = zone_cfg
                dca_cfg = cm.get('anticipatory', 'dca') or {}
                if dca_cfg:
                    regime_config['_dca'] = dca_cfg
            except Exception:
                pass

            ant_scores = self.compute_anticipatory_scores(feature_dict, regime_config)

            # Step 2b: Save feature snapshot for calibrate_anticipatory.py
            # Snapshots accumulate in data/feature_snapshots/ and are used by
            # calibrate_anticipatory.py to recalibrate signal weights over time.
            try:
                snap_dir = os.path.join(
                    os.path.dirname(os.path.dirname(__file__)), "data", "feature_snapshots"
                )
                os.makedirs(snap_dir, exist_ok=True)
                ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                snap_path = os.path.join(snap_dir, f"snapshot_{ts_str}.json")
                with open(snap_path, "w") as _sf:
                    json.dump(
                        {"timestamp": ts_str, "features": feature_dict, "scores": ant_scores},
                        _sf,
                        default=str,
                    )
            except Exception as _snap_err:
                self.logger.debug(f"Snapshot save skipped: {_snap_err}")

            # Step 3: Mechanical decision
            signal, confidence, size_pct, risk_appetite, hold_source = mechanical_decide(
                ant_scores, feature_dict, regime_config,
            )

            # Step 4: Build output compatible with analyze() return format
            reason = (
                f"Mechanical: raw={ant_scores['anticipatory_raw']:.3f} "
                f"struct={ant_scores['structure'].get('direction', 'N/A')}"
                f"({ant_scores['structure'].get('score', 0)}) "
                f"div={ant_scores['divergence'].get('direction', 'N/A')}"
                f"({ant_scores['divergence'].get('score', 0)}) "
                f"flow={ant_scores['order_flow'].get('direction', 'N/A')}"
                f"({ant_scores['order_flow'].get('score', 0)}) "
                f"regime={ant_scores['regime']} ctx={ant_scores['trend_context']}"
            )

            aligned = sum(1 for d in [
                ant_scores["structure"].get("direction"),
                ant_scores["divergence"].get("direction"),
                ant_scores["order_flow"].get("direction"),
            ] if d and d != "NEUTRAL" and d != "N/A" and d != "FADING" and d != "MIXED")

            struct_dir = ant_scores["structure"].get("direction", "NEUTRAL")
            div_dir = ant_scores["divergence"].get("direction", "NEUTRAL")
            flow_dir = ant_scores["order_flow"].get("direction", "NEUTRAL")

            return {
                "signal": signal,
                "confidence": confidence,
                "position_size_pct": size_pct,
                "risk_appetite": risk_appetite,
                "risk_level": ant_scores["risk_env"].get("level", "LOW"),
                "stop_loss": 0.0,
                "take_profit": 0.0,
                "reason": reason,
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "_quality_score": 100,
                "_anticipatory_scores": ant_scores,
                "_timing_assessment": {
                    "timing_verdict": "N/A",
                    "adjusted_confidence": confidence,
                    "reason": "Mechanical mode — no Entry Timing assessment",
                },
                "_timing_rejected": False,
                "_et_exhaustion_tier1": False,
                "_et_exhaustion_tier2": False,
                "_confidence_chain": [
                    {"phase": "mechanical", "confidence": confidence, "source": "anticipatory_scores"},
                ],
                "_structured_debate": {},
                "_memory_conditions_snapshot": {},
                "risk_factors": [],
                "_trend_context": ant_scores.get("trend_context", ""),
                "hold_source": hold_source,
                "judge_decision": {
                    "winning_side": "BULL" if signal == "LONG" else "BEAR" if signal == "SHORT" else "TIE",
                    "rationale": reason,
                    "confluence": {
                        "structure": struct_dir,
                        "divergence": div_dir,
                        "order_flow": flow_dir,
                        "aligned_layers": aligned,
                    },
                    "aligned_layers": aligned,
                },
            }
        except Exception as e:
            self.logger.error(f"Mechanical analysis failed: {e}", exc_info=True)
            return {
                "signal": "HOLD",
                "confidence": "LOW",
                "position_size_pct": 0,
                "risk_appetite": "CONSERVATIVE",
                "risk_level": "HIGH",
                "stop_loss": 0.0,
                "take_profit": 0.0,
                "reason": f"Mechanical analysis error: {e}",
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "_quality_score": 0,
                "_timing_assessment": {"timing_verdict": "N/A", "adjusted_confidence": "LOW", "reason": str(e)},
                "_timing_rejected": False,
                "_et_exhaustion_tier1": False,
                "_et_exhaustion_tier2": False,
                "_confidence_chain": [],
                "_structured_debate": {},
                "_memory_conditions_snapshot": {},
                "risk_factors": [str(e)],
                "judge_decision": {"winning_side": "TIE", "rationale": str(e), "confluence": {}, "aligned_layers": 0},
            }
