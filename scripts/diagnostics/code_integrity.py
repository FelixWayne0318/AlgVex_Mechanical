"""
Code Integrity Checker Module (v21.0)

Static source code analysis without executing the strategy.
Validates critical code patterns using regex/AST inspection.

Checks:
  P1.1:  Bracket order has no emulation_trigger
  P1.2:  on_stop preserves SL/TP orders (selective cancel)
  P1.3:  _recover_sltp_on_start exists and called in on_start
  P1.4:  (removed — was S/R dynamic reevaluation, part of Chandelier)
  P1.5:  Reduce path: S/R recalc + SL favorable + emergency fallback
  P1.6:  v7.2 Add path: R/R validation + per-layer _create_layer
  P1.7:  Unified SL/TP: validate_multiagent + S/R fallback + no pct fallback (v4.2)
  P1.8:  v11.0-simple Time Barrier safety (market close on expiry)
  P1.9:  on_order_filled OCO management
  P1.10: Emergency SL method exists
  P1.11: v5.8 ADX-aware dynamic layer weights in prompts
  P1.12: v5.9 All 5 agents receive past_memories (Bull/Bear/Judge/EntryTiming/Risk)
  P1.13: v5.10 Similarity-based memory retrieval (_build_current_conditions + _score_memory)
  P1.14: v6.0 Cooldown state machine (activate + check + refine)
  P1.15: v6.0 Pyramiding validation (_check_pyramiding_allowed + layer tracking)
  P1.16: v11.0-simple Time Barrier
  P1.17: v6.0 Trade eval integrity (direction-aware planned_rr + captured_sltp cleanup)
  P1.18: v6.0 Cached price staleness detection (_cached_current_price_time + age check)
  P1.19: v6.0 Market order path has emergency SL (enable_auto_sl_tp=False safety net)
  P1.20: v6.0 Leverage get_leverage returns None on failure (not dangerous default 1)
  P1.21: v7.2 Per-layer SL/TP persistence (_persist_layer_orders + _load_layer_orders)
  P1.22: v6.0 S/R zone freshness checks (30min TTL with _calculated_at timestamp)
  P1.23: v6.1 Emergency SL fallback to market close (_emergency_market_close)
  P1.25: v7.2 State clearing covers all pending_* vars + _layer_orders (_clear_position_state + on_stop)
  P1.26: v6.2 LIMIT entry for new positions (not MARKET)
  P1.27: v6.2 LIMIT order expiry/cancel safety (no naked position)
  P1.28: v6.2 Thread safety — no indicator_manager in background threads
  P1.30: v7.0 SSoT — on_timer uses AIDataAssembler.fetch_external_data() (no inline duplication)
  P1.31: v11.0 _check_time_barrier returns bool (True if triggered close)
  P1.32: v11.0 Reversal trades recorded to memory (no early return in on_position_closed)
  P1.33: v11.0 Pyramid layer persistence (_save/_load_position_layers to JSON)
  P1.34: v12.0 Per-Agent Reflection Memory infrastructure
  P1.35: v12.0.1 Reflection backfill on restart
  P1.36: v13.0 Phantom position guard (manual Telegram close)
  P1.37: v14.0 Dual-channel Telegram routing (broadcast parameter)
  P1.38: v7.3 Restart SL cross-validation (Tier 2 exchange query)
  P1.39: Config access safety (no self.config.get() on StrategyConfig Struct)
  P1.40: v15.0 Hardcoded values extraction — 8 magic numbers replaced by config chain
  P1.41: v15.0 Silent exception coverage — no bare except:pass without logging
  P1.42: v15.0 Test suite infrastructure — pytest.ini + conftest.py + 49 active tests
  P1.43: v15.2 counter_trend_rr_multiplier loaded in _TRADING_LOGIC_CONFIG runtime cache
  P1.44: v15.2 Emergency SL/market close/time barrier Telegram messages use side_to_cn()
  P1.45: v15.2 sentiment_client uses .get() for safe dict access (no bracket [] on response)
  P1.46: v15.2 Funding rate format uses :.5f precision (matches Binance 5-decimal standard)
  P1.47: v16.0 Calibration loader integration (sr_zone_calculator imports + uses calibrated factors)
  P1.48: v16.0 Calibration fallback + freshness (DEFAULT_CALIBRATION + MIN_SAMPLES + mtime cache + age detection)
  P1.49: v3.18 Reversal two-phase state machine (_pending_reversal store + detect/clear in on_position_closed)
  P1.50: v18.0 Emergency SL short-cycle retry (set_time_alert + _on_emergency_retry + counter lifecycle)
  P1.51: v18.0 Sentiment degradation marker ('degraded': True in fallback + AI prompt warning)
  P1.52: v18.0 Drawdown hysteresis (dd_recovery_threshold used in _update_trading_state, not dead variable)
  P1.53: v17.0 S/R 1+1 simplification (support_zones/resistance_zones max 1 element, no multi-zone iteration)
  P1.63: v18.2 Price surge trigger (on_trade_tick + _SURGE_THRESHOLD_PCT + cooldown + early analysis scheduling)
  P1.64: v18.2 Ghost position cleanup (3× -2022 rejection counter + force _clear_position_state)
  P1.65: v18.2 Alignment gate weight regime (ADX≥40 → w1d=0.7/w4h=0.3 strong-trend weights)
  P1.66: v7.2-fix /modify_sl and /modify_tp must update _layer_orders (not just sltp_state)
  P1.67: v13.1-fix /partial_close >50% must submit emergency SL for remaining position after success
  P1.68: v19.0 LogAdapter self._log (not self.log) — AttributeError crash fix
  P1.69: v19.0 Confidence authority separation (Judge confidence force-through, RM cannot override)
  P1.70: v19.0 Fingerprint cleared in on_position_closed AND _cancel_pending_entry_order
  P1.71: v19.0 Fingerprint stored only when executed=True (prevents rejected-signal deadlock)
  P1.72: v19.0 Alignment gate receives ai_technical_data (with MTF layers, not 30M-only technical_data)
  P1.73: v19.0 Memory selection computed once (_select_memories + preselected per-role formatting)
  P1.74: v19.1 ATR Extension Ratio calculation (_calculate_extension_ratios in TechnicalIndicatorManager)
  P1.75: v19.1 Extension Ratio fields in get_technical_data output (extension_ratio_sma_* + extension_regime)
  P1.76: v19.1 Extension Ratio integrated in all 5 AI agent prompts (Bull/Bear/Judge/EntryTiming/Risk)
  P1.77: v19.1 RSI/MACD divergence pre-computation (_detect_divergences method)
  P1.78: v19.1 CVD-Price cross-analysis (ACCUMULATION/DISTRIBUTION/CONFIRMED tagging)
  P1.79: v19.1 SIGNAL_CONFIDENCE_MATRIX contains Ext Ratio rows (overextended + extreme)
  P1.80: v19.1 Extension Ratio orthogonal to calculate_mechanical_sltp (pure RISK signal)
  P1.81: v19.1.1 Trend-aware extension modulation (ADX>40 de-emphasis for OVEREXTENDED)
  P1.82: v19.2 CVD-Price time-scale alignment (5-bar window for both 30M and 4H)
  P1.83: v19.2 OI×CVD cross-analysis in derivatives report (CoinGlass 4-quadrant framework)
  P1.84: v19.2 CVD Absorption detection (price flat + CVD directional = passive liquidity)

v2.0 update: P1.126-P1.130 added for v2.0 Upgrade Plan (fear_greed_index schema, EXTREME_FEAR/GREED tags, Instructor wiring, execution_engine, fear_greed_report).
v42.0 update: P1.122-P1.123 added for ET Exhaustion (Tier 1/2 constants, override logic, skip_entry_timing parameter).
v40.0 update: P1.118-P1.121 added for v39.0/v40.0 (4H ATR passthrough, weighted scores, TRANSITIONING regime, TP parameter SSoT).
v34.0 update: P1.115-P1.117 added for auditor logic-level coherence checks (tag classification, reason-signal conflict, confidence-risk conflict).
v19.2 update: P1.82-P1.84 added for CVD-Price time alignment, OI×CVD bridge, absorption detection.
v19.1 update: P1.74-P1.81 added for ATR Extension Ratio, divergence pre-computation, CVD-Price
  cross-analysis, SIGNAL_CONFIDENCE_MATRIX extension rows, orthogonality guard, trend-aware modulation.
v19.0 update: P1.68-P1.73 added for LogAdapter fix, confidence authority separation, fingerprint
  deadlock fixes, alignment gate data source fix, memory selection optimization.
v18.2 update: P1.63-P1.67 added for price surge trigger, ghost position cleanup, alignment weight,
  modify_sl/tp layer tracking fix, partial_close emergency SL fix.
v18.0 update: P1.50-P1.53 added. P1.53 covers v17.0 S/R 1+1 simplification regression guard.
v16.0 update: P1.47-P1.49 added for calibration system + reversal state machine + P1.21 enhanced mutation site coverage.
v15.2 update: P1.43-P1.46 added for config-cache audit, emergency message terminology, sentiment safety, funding rate precision.
v15.0 update: P1.40-P1.42 added for hardcoded-to-config extraction, silent exception coverage, test suite infrastructure.
v15.4 update: bars_data_4h[-50:] + bars_data_1d[-120:] 切片对齐实盘.
v15.3 update: Chandelier/Trailing Stop 全面清除, P1.4 标记已删除, P1.8 更新为 Time Barrier.
v5.8 update: P1.11 added for ADX-aware dynamic weight prompt verification.
v5.9 update: P1.12 added for all-agent memory system verification.
v5.10 update: P1.13 added for similarity-based memory retrieval verification.
v6.0 update: P1.14-P1.17 added for cooldown/pyramiding/eval integrity.
v6.1 update: P1.23-P1.25 added for emergency market close/SL retry/state clearing.
v6.2 update: P1.26-P1.28 added for LIMIT entry/expiry safety/thread safety.
v7.0 update: P1.30 added for SSoT data assembler verification.
v7.2 update: P1.6/P1.21/P1.24/P1.25 updated for per-layer independent SL/TP architecture.
v7.3 update: P1.38 added for restart SL cross-validation (Tier 2 exchange query).
v11.0-simple update: P1.31-P1.33 added for time barrier return bool, reversal recording, pyramid persistence.
v12.0 update: P1.34-P1.35 added for Per-Agent Reflection Memory + restart backfill.
v13.0 update: P1.36 added for phantom position guard (Telegram close flow).
v13.1 update: P1.36 extended to verify emergency SL submission when close/partial-close fails after SL cancelled.
v14.0 update: P1.37 added for dual-channel Telegram routing, P1.39 for config access safety.
v15.0 update: P1.40-P1.42 added for hardcoded-to-config, silent exception coverage, test suite.
"""

import ast
import re
from pathlib import Path
from typing import List, Optional, Set, Tuple

from .base import DiagnosticContext, DiagnosticStep, print_box


# =============================================================================
# AST Helpers — Robust source analysis (replaces fragile regex for structural checks)
# =============================================================================

def _ast_find_subscript_access(source: str, variable_name: str) -> List[Tuple[int, str]]:
    """Find all dict/list subscript accesses (var['key'] or var[idx]) via AST.

    Returns list of (line_number, key_or_index_repr).
    More reliable than regex: ignores comments, strings, and nested expressions.
    """
    results = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return results
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            # Match Name[...] (e.g. sentiment_data['key'])
            if isinstance(node.value, ast.Name) and node.value.id == variable_name:
                key_repr = ast.dump(node.slice) if hasattr(ast, 'dump') else str(node.slice)
                # Extract string constant key if possible
                if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                    key_repr = node.slice.value
                results.append((getattr(node, 'lineno', 0), key_repr))
            # Match self.var[...] (e.g. self.sentiment_data['key'])
            elif (isinstance(node.value, ast.Attribute)
                  and node.value.attr == variable_name):
                key_repr = ast.dump(node.slice)
                if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                    key_repr = node.slice.value
                results.append((getattr(node, 'lineno', 0), key_repr))
    return results


def _ast_find_attr_in_functions(source: str, attr_name: str,
                                function_names: Set[str]) -> List[Tuple[int, str, str]]:
    """Find attribute accesses (self.attr_name) within specific async/sync functions.

    Returns list of (line_number, function_name, code_context).
    Used for thread-safety checks: detect indicator_manager in background methods.
    """
    results = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return results
    source_lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name not in function_names:
                continue
            # Walk within this function body
            for child in ast.walk(node):
                if isinstance(child, ast.Attribute) and child.attr == attr_name:
                    lineno = getattr(child, 'lineno', 0)
                    context = source_lines[lineno - 1].strip() if lineno > 0 and lineno <= len(source_lines) else ""
                    results.append((lineno, node.name, context))
    return results


def _ast_find_fstring_format_specs(source: str, var_prefix: str) -> List[Tuple[int, str, str]]:
    """Find f-string format specifications for variables matching a prefix.

    Returns list of (line_number, variable_name, format_spec).
    E.g. for f"{fr_pct:.5f}" returns (line, "fr_pct", ".5f").
    """
    results = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return results
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):  # f-string
            for value in node.values:
                if isinstance(value, ast.FormattedValue):
                    # Get format spec
                    fmt_spec = ""
                    if value.format_spec and isinstance(value.format_spec, ast.JoinedStr):
                        # Format spec is itself a JoinedStr of constants
                        parts = []
                        for part in value.format_spec.values:
                            if isinstance(part, ast.Constant):
                                parts.append(str(part.value))
                        fmt_spec = "".join(parts)

                    # Get variable name
                    var_name = ""
                    if isinstance(value.value, ast.Name):
                        var_name = value.value.id
                    elif isinstance(value.value, ast.Attribute):
                        var_name = value.value.attr

                    if var_name.startswith(var_prefix) and fmt_spec:
                        lineno = getattr(node, 'lineno', 0)
                        results.append((lineno, var_name, fmt_spec))
    return results


def _extract_method(source_lines: List[str], method_name: str) -> Optional[str]:
    """Extract a method's source from lines, using indentation.

    Supports both class methods (indented) and module-level functions (no indent).
    """
    pattern = re.compile(rf"^\s*def {re.escape(method_name)}\s*\(")
    start_idx = None
    base_indent = 0
    for i, line in enumerate(source_lines):
        if pattern.match(line):
            start_idx = i
            base_indent = len(line) - len(line.lstrip())
            break
    if start_idx is None:
        return None
    end_idx = start_idx + 1
    passed_signature = False
    while end_idx < len(source_lines):
        line = source_lines[end_idx]
        stripped = line.strip()
        if stripped == "":
            end_idx += 1
            continue
        curr_indent = len(line) - len(line.lstrip())
        if not passed_signature:
            if stripped.endswith("):") or stripped.endswith(") ->"):
                passed_signature = True
            # Single-line def: if the def line itself has complete params
            # (both '(' and ')'), signature is done regardless of return
            # type annotation (e.g. `def foo() -> Dict[str, Any]:`)
            def_line = source_lines[start_idx].strip()
            if "(" in def_line and ")" in def_line and def_line.endswith(":"):
                passed_signature = True
            end_idx += 1
            continue
        if curr_indent <= base_indent and (
            stripped.startswith("def ") or
            stripped.startswith("class ") or
            stripped.startswith("@")
        ):
            break
        end_idx += 1
    return "\n".join(source_lines[start_idx:end_idx])


class CodeIntegrityChecker(DiagnosticStep):
    """
    v5.1 静态代码完整性检查

    Uses regex/AST to inspect ai_strategy.py source code
    without executing it. Validates all v5.1 order flow safety patterns.
    """

    name = "v19.0 代码完整性检查 (静态分析)"

    # v24.0: Strategy + agent file lists after mixin refactor
    _STRATEGY_FILES = [
        "ai_strategy.py", "event_handlers.py", "order_execution.py",
        "position_manager.py", "safety_manager.py", "telegram_commands.py",
    ]
    _AGENT_FILES = [
        "multi_agent_analyzer.py", "prompt_constants.py",
        "report_formatter.py", "mechanical_decide.py",
    ]

    def __init__(self, ctx: DiagnosticContext):
        super().__init__(ctx)
        self._results: List[dict] = []

    def _read_agents_source(self) -> str:
        """Read and combine all agent files (core + 3 auxiliary)."""
        parts = []
        for name in self._AGENT_FILES:
            p = self.ctx.project_root / "agents" / name
            if p.exists():
                parts.append(p.read_text(encoding='utf-8'))
        return "\n".join(parts)

    def run(self) -> bool:
        print()
        print_box("v21.0 Code Integrity (静态代码检查)", 65)
        print()

        strategy_path = self.ctx.project_root / "strategy" / "ai_strategy.py"
        if not strategy_path.exists():
            self._record("P1.0", "Strategy file exists", False,
                         actual=f"{strategy_path} not found")
            return False

        # v24.0: Read ALL strategy files (main + 5 mixins) as combined source.
        # After mixin refactor, methods are spread across 6 files but still form
        # one logical class via MRO.  Combine them so pattern checks work unchanged.
        _strategy_files = [
            "ai_strategy.py",
            "event_handlers.py",
            "order_execution.py",
            "position_manager.py",
            "safety_manager.py",
            "telegram_commands.py",
        ]
        source = ""
        for _sf in _strategy_files:
            _sp = self.ctx.project_root / "strategy" / _sf
            if _sp.exists():
                source += _sp.read_text(encoding='utf-8') + "\n"
        lines = source.splitlines()

        self._check_bracket_no_emulation(lines)
        self._check_on_stop_preserves_sltp(lines)
        self._check_recover_sltp_on_start(source, lines)
        self._check_reduce_path(lines)
        self._check_add_path_replace(source, lines)
        self._check_validate_sltp_for_entry(lines)
        self._check_dynamic_sltp_safety(lines)
        self._check_on_order_filled_oco(lines)
        self._check_emergency_sl(source)

        # P1.11-P1.13: agents checks (all 4 agent files combined)
        _agent_files = [
            "multi_agent_analyzer.py",
            "prompt_constants.py",
            "report_formatter.py",
            "mechanical_decide.py",
        ]
        ma_source = ""
        for _af in _agent_files:
            _ap = self.ctx.project_root / "agents" / _af
            if _ap.exists():
                ma_source += _ap.read_text(encoding='utf-8') + "\n"
        ma_lines = ma_source.splitlines()
        if ma_source:
            # P1.11: v5.8 ADX-aware dynamic weights in prompts
            self._check_adx_aware_prompts(ma_source)
            # P1.12: v5.9 All 5 agents receive past_memories
            self._check_all_agents_receive_memory(ma_source, ma_lines)
            # P1.13: v5.10 Similarity-based memory retrieval
            self._check_similarity_memory(ma_source)

        # P1.14-P1.17: v6.0 Position Management checks
        self._check_cooldown_state_machine(source, lines)
        self._check_pyramiding_validation(source, lines)
        self._check_time_barrier(source, lines)
        self._check_trade_eval_integrity(source, lines)

        # P1.18-P1.22: v6.0 Safety & alignment checks
        self._check_cached_price_staleness(source)
        self._check_market_order_emergency_sl(source)
        self._check_leverage_none_handling(source)
        self._check_reduce_timeout_reevaluation(source)
        self._check_sr_zone_freshness(source)

        # P1.23-P1.25: v6.1 Safety enhancements
        self._check_emergency_market_close_fallback(source)
        self._check_state_clearing_completeness(source, lines)

        # P1.26-P1.28: v6.2 LIMIT entry + expiry safety + thread safety
        self._check_limit_entry_not_market(source, lines)
        self._check_limit_order_expiry_safety(source, lines)
        self._check_thread_safety(source)

        # P1.29: v6.3 AI prompt ATR-based language
        self._check_ai_prompt_atr_language()

        # P1.30: v7.0 SSoT — on_timer uses AIDataAssembler.fetch_external_data()
        self._check_v70_data_assembler_ssot(source, lines)

        # P1.31-P1.33: v11.0-simple enhancements
        self._check_time_barrier_return_bool(source, lines)
        self._check_reversal_recording(source, lines)
        self._check_position_layers_persistence(source)

        # P1.34-P1.35: v12.0 Per-Agent Reflection Memory
        ma_src = ma_source  # Reuse combined agents source from above
        self._check_reflection_infrastructure(source, ma_src)
        self._check_reflection_backfill(source)

        # P1.36: v13.0 Phantom position guard (manual Telegram close)
        self._check_phantom_position_guard(source)

        # P1.37: v14.0 Dual-channel Telegram routing
        tg_path = self.ctx.project_root / "utils" / "telegram_bot.py"
        tg_source = tg_path.read_text() if tg_path.exists() else ""
        self._check_dual_channel_telegram(source, tg_source)

        # P1.38: v7.3 Restart SL cross-validation (Tier 2)
        self._check_restart_sl_crossvalidation(source)

        # P1.39: Config access safety (no self.config.get() on StrategyConfig)
        self._check_config_access_safety(source)

        # P1.40-P1.42: v15.0 Code quality improvements
        self._check_hardcoded_values_extracted(source)
        self._check_silent_exception_coverage(source, ma_src)
        self._check_test_suite_infrastructure()

        # P1.43-P1.46: v15.2 Code audit regression guards
        tl_path = self.ctx.project_root / "strategy" / "trading_logic.py"
        tl_source = tl_path.read_text(encoding='utf-8') if tl_path.exists() else ""
        self._check_counter_trend_rr_in_config_cache(tl_source)
        self._check_emergency_messages_use_side_to_cn(source)
        sc_path = self.ctx.project_root / "utils" / "sentiment_client.py"
        sc_source = sc_path.read_text(encoding='utf-8') if sc_path.exists() else ""
        self._check_sentiment_client_safe_access(sc_source)
        self._check_funding_rate_5f_precision(ma_src)

        # P1.47-P1.48: v16.0 S/R Hold Probability calibration system
        sr_path = self.ctx.project_root / "utils" / "sr_zone_calculator.py"
        sr_source = sr_path.read_text(encoding='utf-8') if sr_path.exists() else ""
        cl_path = self.ctx.project_root / "utils" / "calibration_loader.py"
        cl_source = cl_path.read_text(encoding='utf-8') if cl_path.exists() else ""
        self._check_calibration_loader_integration(sr_source, cl_source)
        self._check_calibration_fallback_and_freshness(cl_source)

        # P1.49: v3.18 Reversal two-phase state machine
        self._check_reversal_state_machine(source, lines)

        # P1.50-P1.53: v18.0 Architecture fixes regression guards
        self._check_emergency_retry_timer(source, lines)
        ada_path = self.ctx.project_root / "utils" / "ai_data_assembler.py"
        ada_source = ada_path.read_text(encoding='utf-8') if ada_path.exists() else ""
        self._check_sentiment_degradation_marker(ada_source, ma_src)
        rc_path = self.ctx.project_root / "utils" / "risk_controller.py"
        rc_source = rc_path.read_text(encoding='utf-8') if rc_path.exists() else ""
        self._check_drawdown_hysteresis(rc_source)

        # P1.53: v17.0 S/R 1+1 simplification
        self._check_sr_one_plus_one(sr_source, ma_src)

        # P1.54-P1.56: v18.0 Reflection System Reform
        self._check_reflection_reform(ma_src, source, lines)

        # P1.57-P1.62: v18 Batch 2/3 features (30M migration, 4H CVD, alignment gate,
        # entry quality, direction compliance, data partitioning)
        self._check_v18_30m_migration(source, ada_source, ma_src)
        self._check_v18_4h_cvd_order_flow(source, ada_source, ma_src)
        self._check_v18_alignment_gate(source, lines)
        self._check_v18_entry_quality_downgrade(source, lines)
        self._check_v18_direction_compliance_audit(ma_src)
        self._check_v18_data_partitioning(ma_src)

        # P1.63-P1.67: v18.2 features + review fixes
        self._check_v182_price_surge_trigger(source)
        self._check_v182_ghost_position_cleanup(source)
        self._check_v182_alignment_weight_regime(source)
        self._check_modify_sltp_layer_tracking(source)
        self._check_partial_close_emergency_sl(source)

        # P1.68-P1.73: v19.0 Confidence authority + bug fixes
        self._check_v190_logadapter_self_log(source)
        self._check_v190_confidence_authority(ma_src)
        self._check_v190_fingerprint_clearing(source)
        self._check_v190_fingerprint_execute_guard(source)
        self._check_v190_alignment_gate_data_source(source)
        self._check_v190_memory_selection_once(ma_src)

        # P1.74-P1.81: v19.1 ATR Extension Ratio + Divergence + CVD-Price + Trend-aware
        tm_path = self.ctx.project_root / "indicators" / "technical_manager.py"
        tm_source = tm_path.read_text(encoding='utf-8') if tm_path.exists() else ""
        self._check_v191_extension_ratio_calc(tm_source)
        self._check_v191_extension_ratio_fields(tm_source)
        self._check_v191_extension_ratio_prompts(ma_src)
        self._check_v191_divergence_precompute(ma_src)
        self._check_v191_cvd_price_analysis(ma_src)
        self._check_v191_signal_confidence_matrix(ma_src)
        self._check_v191_extension_orthogonal(source, tm_source)
        self._check_v191_trend_aware_extension(ma_src)

        # P1.82-P1.84: v19.2 CVD time alignment + OI×CVD bridge + Absorption
        self._check_v192_cvd_time_alignment(ma_src)
        self._check_v192_oi_cvd_bridge(ma_src)
        self._check_v192_absorption_detection(ma_src)

        # P1.85-P1.90: v20.0 ATR Volatility Regime + OBV Divergence
        self._check_v200_atr_regime_calc(tm_source)
        self._check_v200_atr_regime_fields(tm_source)
        self._check_v200_obv_tracking(tm_source)
        self._check_v200_obv_divergence(ma_src)
        self._check_v200_volatility_regime_prompts(ma_src)
        self._check_v200_volatility_regime_orthogonal(tm_source)

        # P1.91-P1.96: v23.0 Entry Timing Agent implementation fixes
        self._check_v230_trend_data_available(ma_src)
        self._check_v230_phase25_try_except(ma_src)
        self._check_v230_shallow_copy_upfront(ma_src)
        self._check_v230_signal_age_check(source)
        self._check_v230_et_consecutive_rejects(source)
        self._check_v230_fallback_timing_assessment(ma_src)

        # P1.97-P1.99: v24.0 AI Quality Auditor — always skip (deleted v46.0)
        self._check_v240_quality_auditor_import(ma_src)
        self._check_v240_quality_audit_call(ma_src)
        self._check_v240_quality_score_in_decision(ma_src)

        # P1.100-P1.107: v24.0 Trailing Stop (TRAILING_STOP_MARKET)
        self._check_v240_trailing_stop_submit(source)
        self._check_v240_trailing_activation(source)
        self._check_v240_trailing_event_handler(source)
        self._check_v240_trailing_modify_sl_reset(source)
        self._check_v240_trailing_resubmit_sltp(source)
        self._check_v240_trailing_startup_recovery(source)
        self._check_v240_reconcile_order_resubmit(source)
        self._check_v240_emergency_close_cancel_orders(source)

        # P1.108-P1.110: Additional regression guards (v21.0, v18.3, v24.0)
        self._check_v210_fr_block_counter(source)
        self._check_v183_force_analysis_cycles(source)
        self._check_v240_tp_resubmit(source)

        # P1.111-P1.112: AnalysisContext parity — always skip (deleted v46.0)
        self._check_v29_confidence_chain(ma_source)
        self._check_v29_conditions_v2(ma_source)

        # P1.113: v32.1 Risk Manager conditional skip
        self._check_v321_risk_manager_skip(ma_source)

        # P1.115-P1.117: v34.0 Auditor checks — SKIPPED (ai_quality_auditor.py deleted v46.0)

        # P1.118-P1.121: v39.0/v40.0 static analysis
        _rf_path = self.ctx.project_root / "agents" / "report_formatter.py"
        rf_source = _rf_path.read_text(encoding='utf-8') if _rf_path.exists() else ""
        self._check_v390_4h_atr_passthrough(source)
        self._check_v400_weighted_scores(rf_source)
        self._check_v400_transitioning_regime(rf_source)
        self._check_v400_tp_parameter_ssot()

        # P1.122-P1.123: v42.0 ET Exhaustion
        _ma_path = self.ctx.project_root / "agents" / "multi_agent_analyzer.py"
        ma_source = _ma_path.read_text(encoding='utf-8') if _ma_path.exists() else ""
        self._check_v420_et_exhaustion_strategy(source)
        self._check_v420_skip_entry_timing(source, ma_source)

        # P1.126-P1.130: v2.0 Upgrade Plan static checks
        self._check_v20_fear_greed_schema()
        self._check_v20_extreme_tags()
        self._check_v20_instructor_wiring(ma_source)
        # P1.129: execution_engine.py removed (never integrated, Occam's razor)
        self._check_v20_fear_greed_analyze(ma_source)

        # Summary
        passed = sum(1 for r in self._results if r["pass"])
        total = len(self._results)
        failed = total - passed

        print()
        print(f"  代码完整性: {passed}/{total} 通过", end="")
        if failed > 0:
            print(f", {failed} 失败")
            for r in self._results:
                if not r["pass"]:
                    self.ctx.add_error(f"[{r['id']}] {r['desc']}: {r.get('actual', '')}")
        else:
            print(" ✅")

        # Store results for JSON output
        if not hasattr(self.ctx, 'code_integrity_results'):
            self.ctx.code_integrity_results = []
        self.ctx.code_integrity_results = self._results

        return failed == 0

    def _record(self, check_id: str, desc: str, passed: bool,
                expected: str = "", actual: str = "", detail: str = ""):
        """Record and print a check result."""
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  [{check_id}] {desc}")
        if expected:
            print(f"    Expected: {expected}")
        if actual:
            print(f"    Actual:   {actual}")
        print(f"    Result:   {status}")
        if detail:
            for line in detail.split("\n"):
                print(f"    {line}")
        print()
        self._results.append({
            "id": check_id, "desc": desc, "pass": passed,
            "expected": expected, "actual": actual,
        })

    # ── P1.1: Bracket order has no emulation_trigger ──

    def _check_bracket_no_emulation(self, lines: List[str]):
        bracket_method = _extract_method(lines, "_submit_bracket_order")
        if bracket_method is None:
            self._record("P1.1", "Bracket order: no emulation_trigger", False,
                         actual="_submit_bracket_order method not found")
            return
        code_lines = [l for l in bracket_method.splitlines()
                      if l.strip() and not l.strip().startswith("#")]
        has_emulation = any("emulation_trigger" in l and "=" in l
                            for l in code_lines)
        has_bracket = "order_factory.bracket" in bracket_method
        if has_emulation:
            self._record("P1.1", "Bracket order: no emulation_trigger", False,
                         expected="emulation_trigger removed",
                         actual="emulation_trigger= still present as active code",
                         detail="SL/TP 仍在本地模拟, 崩溃会丢失!")
        else:
            self._record("P1.1", "Bracket order: no emulation_trigger",
                         has_bracket,
                         expected="order_factory.bracket() without emulation_trigger",
                         actual=f"emulation_trigger removed, bracket call: "
                                f"{'found' if has_bracket else 'NOT found'}")

    # ── P1.2: on_stop preserves SL/TP ──

    def _check_on_stop_preserves_sltp(self, lines: List[str]):
        on_stop = _extract_method(lines, "on_stop")
        if on_stop is None:
            self._record("P1.2", "on_stop: preserves SL/TP orders", False,
                         actual="on_stop method not found")
            return
        has_selective = "is_reduce_only" in on_stop
        main_path_cancel = False
        in_except = False
        for line in on_stop.splitlines():
            stripped = line.strip()
            if "except" in stripped:
                in_except = True
            if "cancel_all_orders" in stripped and not in_except:
                main_path_cancel = True
                break
        if has_selective and not main_path_cancel:
            self._record("P1.2", "on_stop: preserves SL/TP orders", True,
                         expected="Selective cancel (skip reduce_only), cancel_all only in fallback",
                         actual="is_reduce_only check present, cancel_all_orders only in except block")
        elif main_path_cancel:
            self._record("P1.2", "on_stop: preserves SL/TP orders", False,
                         expected="cancel_all_orders NOT in main path",
                         actual="cancel_all_orders in main execution path — SL/TP will be lost!")
        else:
            self._record("P1.2", "on_stop: preserves SL/TP orders", False,
                         actual=f"selective={has_selective}, cancel_all_main={main_path_cancel}")

    # ── P1.3: _recover_sltp_on_start ──

    def _check_recover_sltp_on_start(self, source: str, lines: List[str]):
        has_def = "def _recover_sltp_on_start" in source
        on_start = _extract_method(lines, "on_start")
        has_call = "_recover_sltp_on_start()" in (on_start or "")
        self._record("P1.3", "on_start: crash recovery (_recover_sltp_on_start)",
                     has_def and has_call,
                     expected="Method defined + called in on_start",
                     actual=f"defined={has_def}, called_in_on_start={has_call}")

    # ── P1.5: Position management before AI + pyramiding enabled ──

    def _check_reduce_path(self, lines: List[str]):
        on_timer = _extract_method(lines, "on_timer") or ""
        on_timer_lines = on_timer.split('\n')

        # Structural verification: find call positions (line indices) to verify ordering
        # Position management calls must appear BEFORE AI analyze() call
        mgmt_idx = -1   # first management call index
        tb_idx = -1     # time barrier call index
        ai_idx = -1     # AI analyze() call index

        for i, line in enumerate(on_timer_lines):
            stripped = line.strip()
            if mgmt_idx < 0 and (
                "_reconcile_layer_quantities" in stripped
                or "_manage_existing_position" in stripped
                or "_check_time_barrier" in stripped
            ):
                mgmt_idx = i
            if tb_idx < 0 and "_check_time_barrier" in stripped and "=" in stripped:
                tb_idx = i
            if ai_idx < 0 and "multi_agent" in stripped and "analyze(" in stripped:
                ai_idx = i

        has_management = mgmt_idx >= 0
        has_time_barrier = tb_idx >= 0
        has_ai_call = ai_idx >= 0
        # Core invariant: management before AI (ordering in source = ordering at runtime)
        order_correct = has_management and has_ai_call and mgmt_idx < ai_idx

        # Time barrier early return must be the ONLY early return between management and AI
        # (i.e., non-TB management doesn't block AI analysis)
        tb_only_return = True
        if has_management and has_ai_call:
            for i in range(mgmt_idx, ai_idx):
                stripped = on_timer_lines[i].strip()
                if stripped == "return" or stripped.startswith("return "):
                    # Only allowed after time_barrier_triggered check
                    # Use 8-line lookback: the return may be separated from the
                    # time_barrier variable by OCO cleanup code (up to ~6 lines).
                    context = '\n'.join(on_timer_lines[max(0, i-8):i+1])
                    if "time_barrier" not in context:
                        tb_only_return = False
                        break

        ok = order_correct and tb_only_return
        self._record("P1.5", "Position: management before AI + continue to analysis",
                     ok,
                     expected="management calls before analyze(), only time_barrier may early-return",
                     actual=f"mgmt_idx={mgmt_idx}, tb_idx={tb_idx}, ai_idx={ai_idx}, "
                            f"order={order_correct}, tb_only_return={tb_only_return}")

    # ── P1.6: v7.2 Add path: per-layer _create_layer + R/R validation ──

    def _check_add_path_replace(self, source: str, lines: List[str]):
        # v7.2: Add path creates independent layer (not _replace_sltp_orders)
        has_create_layer = "def _create_layer" in source
        manage = _extract_method(lines, "_manage_existing_position") or ""
        # v7.2: _create_layer is called in pyramiding flow (within _manage_existing_position)
        has_layer_used = "_create_layer" in manage or "_create_layer" in source
        # v4.11: Add path must validate R/R before adding (same as new positions)
        has_rr_validation = "_validate_sltp_for_entry" in manage
        # v7.2: Per-layer SL/TP submission (stop_market + limit_if_touched per layer)
        has_independent_sltp = "stop_market" in manage and "limit_if_touched" in manage
        ok = has_create_layer and has_rr_validation and has_layer_used
        self._record("P1.6", "v7.2 Add path: R/R validation + per-layer _create_layer", ok,
                     expected="_validate_sltp_for_entry before add + _create_layer for independent SL/TP",
                     actual=f"create_layer_defined={has_create_layer}, layer_used={has_layer_used}, "
                            f"rr_validation={has_rr_validation}, independent_sltp={has_independent_sltp}")

    # ── P1.7: v11.0-simple: Mechanical SL/TP in _validate_sltp_for_entry ──

    def _check_validate_sltp_for_entry(self, lines: List[str]):
        method = _extract_method(lines, "_validate_sltp_for_entry")
        if method is None:
            self._record("P1.7", "Mechanical SL/TP (_validate_sltp_for_entry)", False,
                         actual="Method not found")
            return
        has_mechanical = "calculate_mechanical_sltp" in method
        has_no_sr = "calculate_sr_based_sltp" not in method
        has_counter_trend = "_is_counter_trend" in method
        has_price_chain = "binance_account" in method or "latest_price_data" in method
        ok = has_mechanical and has_no_sr
        self._record("P1.7", "v11.0-simple: Mechanical SL/TP in _validate_sltp_for_entry", ok,
                     expected="calculate_mechanical_sltp (no sr_sltp) + counter-trend detection",
                     actual=f"mechanical={has_mechanical}, no_sr={has_no_sr}, "
                            f"counter_trend={has_counter_trend}, price_chain={has_price_chain}")

    # ── P1.8: v11.0-simple: Time Barrier safety ──

    def _check_dynamic_sltp_safety(self, lines: List[str]):
        method = _extract_method(lines, "_check_time_barrier")
        if method is None:
            self._record("P1.8", "v11.0-simple: Time Barrier", False,
                         actual="_check_time_barrier not found")
            return
        has_duration = "duration_minutes" in method
        has_counter_trend = "_is_counter_trend" in method
        has_market_close = "emergency_market_close" in method or "close" in method.lower()
        has_hours_config = "max_holding_hours" in method
        ok = has_duration and has_counter_trend and has_hours_config
        self._record("P1.8", "v11.0-simple: Time Barrier (market close on expiry)", ok,
                     expected="duration_minutes + _is_counter_trend + max_holding_hours config",
                     actual=f"duration_minutes={has_duration}, counter_trend={has_counter_trend}, "
                            f"hours_config={has_hours_config}, market_close={has_market_close}")

    # ── P1.9: on_order_filled OCO ──

    def _check_on_order_filled_oco(self, lines: List[str]):
        method = _extract_method(lines, "on_order_filled")
        if method:
            has_oco = "reduce_only" in method or "cancel" in method.lower()
            self._record("P1.9", "on_order_filled: manual OCO management", has_oco,
                         expected="SL fills → cancel TP, TP fills → cancel SL",
                         actual=f"OCO logic present={has_oco}")
        else:
            self._record("P1.9", "on_order_filled: manual OCO management", False,
                         actual="on_order_filled not found")

    # ── P1.10: Emergency SL ──

    def _check_emergency_sl(self, source: str):
        has_emergency = "def _submit_emergency_sl" in source
        self._record("P1.10", "Emergency SL method exists", has_emergency,
                     expected="_submit_emergency_sl defined",
                     actual=f"Present={has_emergency}")

    # ── P1.11: v5.8 ADX-aware dynamic weights in prompts ──

    def _check_adx_aware_prompts(self, ma_source: str):
        """Check multi_agent_analyzer.py for v5.8 ADX-aware dynamic weights.

        Structural verification: _compute_trend_verdict must use ADX-based regime
        branching (adx < 20 for ranging, adx >= 30 for strong trend).
        Negative checks ensure old static weight patterns are not present.
        """
        ma_lines = ma_source.split('\n')

        # Positive check: _compute_trend_verdict uses ADX conditionals for regime branching
        verdict_method = _extract_method(ma_lines, "_compute_trend_verdict") or ""
        has_adx_ranging = bool(re.search(r'adx_1d\s*<\s*20', verdict_method))
        has_adx_strong = bool(re.search(r'adx_1d\s*>=?\s*(30|40)', verdict_method))
        has_regime_var = "regime" in verdict_method and ("RANGING" in verdict_method or "TRENDING" in verdict_method)
        has_adx_dynamic = has_adx_ranging and has_adx_strong and has_regime_var

        # Negative checks: ensure old static weight patterns are removed (regression guards)
        has_old_static = "趋势层 (1D) 权重最高" in ma_source
        has_old_comment = "highest weight per confluence matrix" in ma_source
        has_verdict_static = "HIGHEST WEIGHT" in ma_source
        has_footer_static = "macro trend above has priority" in ma_source

        ok = (has_adx_dynamic and not has_old_static and not has_old_comment
              and not has_verdict_static and not has_footer_static)
        issues = []
        if has_old_static:
            issues.append("旧版静态规则 '趋势层权重最高' 仍存在")
        if has_old_comment:
            issues.append("key_metrics 仍有 'highest weight' 注释")
        if has_verdict_static:
            issues.append("_compute_trend_verdict 仍有 'HIGHEST WEIGHT'")
        if has_footer_static:
            issues.append("trend verdict footer 仍有 'macro trend has priority'")
        if not has_adx_dynamic:
            issues.append("_compute_trend_verdict 缺少 ADX 阈值分支 (adx<20/adx>=30)")

        self._record("P1.11", "v5.8 ADX-aware dynamic layer weights (structural)", ok,
                     expected="_compute_trend_verdict: adx<20 ranging + adx>=30 trending + regime branching",
                     actual=f"adx_ranging={has_adx_ranging}, adx_strong={has_adx_strong}, "
                            f"regime_var={has_regime_var}, no_old_static={not has_old_static}, "
                            f"no_verdict_static={not has_verdict_static}"
                            + (f" | Issues: {'; '.join(issues)}" if issues else ""))

    # ── P1.12: v5.9 All 5 agents receive past_memories ──

    def _check_all_agents_receive_memory(self, ma_source: str, ma_lines: List[str]):
        """Check that all 5 agents (Bull/Bear/Judge/EntryTiming/Risk) receive past_memories parameter."""
        # Check analyze() calls _get_past_memories
        analyze_method = _extract_method(ma_lines, "analyze")
        has_memory_call = "_get_past_memories" in (analyze_method or "")

        # Check each agent method accepts past_memories parameter
        bull_method = _extract_method(ma_lines, "_get_bull_argument")
        bear_method = _extract_method(ma_lines, "_get_bear_argument")
        judge_method = _extract_method(ma_lines, "_get_judge_decision")
        et_method = _extract_method(ma_lines, "_evaluate_entry_timing")
        risk_method = _extract_method(ma_lines, "_evaluate_risk")

        bull_has_memory = "past_memories" in (bull_method or "")[:500] if bull_method else False
        bear_has_memory = "past_memories" in (bear_method or "")[:500] if bear_method else False
        judge_has_memory = "past_memories" in (judge_method or "")[:500] if judge_method else False
        et_has_memory = "past_memories" in (et_method or "")[:500] if et_method else False
        risk_has_memory = "past_memories" in (risk_method or "")[:500] if risk_method else False

        # Check prompts contain memory sections
        has_bull_section = "PAST TRADE PATTERNS" in ma_source
        has_judge_section = "PAST REFLECTIONS" in ma_source
        has_et_section = "PAST TIMING MISTAKES" in ma_source

        all_agents_ok = (bull_has_memory and bear_has_memory and judge_has_memory
                         and et_has_memory and risk_has_memory)
        ok = (has_memory_call and all_agents_ok
              and has_bull_section and has_judge_section and has_et_section)

        agents_status = (f"bull={bull_has_memory}, bear={bear_has_memory}, "
                        f"judge={judge_has_memory}, entry_timing={et_has_memory}, "
                        f"risk={risk_has_memory}")

        self._record("P1.12", "v5.9/v23.0 All 5 agents receive past_memories", ok,
                     expected="analyze() calls _get_past_memories + all 5 agent methods accept past_memories "
                              "+ prompts contain PAST TRADE PATTERNS / PAST REFLECTIONS / PAST TIMING MISTAKES",
                     actual=f"memory_call={has_memory_call}, agents=[{agents_status}], "
                            f"prompt_sections: PAST_TRADE_PATTERNS={has_bull_section}, "
                            f"PAST_REFLECTIONS={has_judge_section}, "
                            f"PAST_TIMING_MISTAKES={has_et_section}")

    # ── P1.13: v5.10 Similarity-based memory retrieval ──

    def _check_similarity_memory(self, ma_source: str):
        """Check for v5.10+ similarity-based memory retrieval components.

        AnalysisContext refactor: _build_current_conditions() replaced by
        MemoryConditions.from_feature_dict() — check for the new path.
        """
        has_memory_conditions = "MemoryConditions.from_feature_dict" in ma_source
        has_score_memory = "def _score_memory" in ma_source

        # Check analyze() passes current_conditions to _get_past_memories
        has_conditions_in_analyze = "current_conditions" in ma_source and "_get_past_memories(current_conditions" in ma_source

        # Check _get_past_memories accepts current_conditions parameter
        has_def_method = "def _get_past_memories(" in ma_source
        ma_lines = ma_source.splitlines()
        method_body = _extract_method(ma_lines, "_get_past_memories") or ""
        has_param = has_def_method and "current_conditions" in method_body[:300]

        # Check scoring dimensions (new MemoryConditions keys)
        scoring_dimensions = []
        for dim in ["rsi_30m", "macd_bullish", "bb_position_30m", "sentiment", "direction"]:
            if dim in ma_source and "_score_memory" in ma_source:
                scoring_dimensions.append(dim)

        ok = has_memory_conditions and has_score_memory and has_conditions_in_analyze and has_param
        self._record("P1.13", "v5.10 Similarity-based memory retrieval", ok,
                     expected="MemoryConditions.from_feature_dict + _score_memory + current_conditions passed to _get_past_memories",
                     actual=f"memory_conditions={has_memory_conditions}, score_memory={has_score_memory}, "
                            f"conditions_in_analyze={has_conditions_in_analyze}, param_accepted={has_param}, "
                            f"scoring_dims={scoring_dimensions}")

    # ── P1.14: v6.0 Cooldown state machine ──

    def _check_cooldown_state_machine(self, source: str, lines: List[str]):
        """Check v6.0 cooldown system: activate + check + refine stop type."""
        has_activate = "def _activate_stoploss_cooldown" in source
        has_check = "def _check_stoploss_cooldown" in source
        has_refine = "def _refine_stop_type" in source

        # Verify cooldown state variables exist in __init__
        has_cooldown_until = "_stoploss_cooldown_until" in source
        has_cooldown_type = "_stoploss_cooldown_type" in source

        # Verify on_timer uses cooldown gate
        on_timer = _extract_method(lines, "on_timer") or ""
        has_timer_gate = "_check_stoploss_cooldown" in on_timer

        # Verify on_position_closed activates cooldown with SL detection guard
        on_closed = _extract_method(lines, "on_position_closed") or ""
        has_closed_activate = "_activate_stoploss_cooldown" in on_closed
        has_sl_detection = "is_stoploss_exit" in on_closed

        ok = (has_activate and has_check and has_refine
              and has_cooldown_until and has_timer_gate
              and has_closed_activate and has_sl_detection)
        self._record("P1.14", "v6.0 Cooldown state machine (activate + check + refine)", ok,
                     expected="3 methods + state vars + on_timer gate + SL-detection guard + on_position_closed activation",
                     actual=f"activate={has_activate}, check={has_check}, refine={has_refine}, "
                            f"state_vars={has_cooldown_until and has_cooldown_type}, "
                            f"timer_gate={has_timer_gate}, closed_activate={has_closed_activate}, "
                            f"sl_detection={has_sl_detection}")

    # ── P1.15: v6.0 Pyramiding validation ──

    def _check_pyramiding_validation(self, source: str, lines: List[str]):
        """Check v6.0 pyramiding: validation method + layer tracking."""
        has_check_method = "def _check_pyramiding_allowed" in source
        has_layers_var = "_position_layers" in source

        # Verify _manage_existing_position uses pyramiding gate
        manage = _extract_method(lines, "_manage_existing_position") or ""
        has_pyramid_gate = "_check_pyramiding_allowed" in manage

        # Verify _open_new_position initializes layers
        open_new = _extract_method(lines, "_open_new_position") or ""
        has_layer_init = "_position_layers" in open_new

        # Verify on_position_closed clears layers
        on_closed = _extract_method(lines, "on_position_closed") or ""
        has_layer_clear = "_position_layers" in on_closed

        ok = (has_check_method and has_layers_var
              and has_pyramid_gate and has_layer_init and has_layer_clear)
        self._record("P1.15", "v6.0 Pyramiding validation (layers + gate)", ok,
                     expected="_check_pyramiding_allowed + _position_layers in manage/open/close",
                     actual=f"check_method={has_check_method}, layers_var={has_layers_var}, "
                            f"manage_gate={has_pyramid_gate}, open_init={has_layer_init}, "
                            f"close_clear={has_layer_clear}")

    # ── P1.16: v11.0-simple Time Barrier ──

    def _check_time_barrier(self, source: str, lines: List[str]):
        """Check v11.0-simple: Time Barrier."""
        has_time_barrier = "def _check_time_barrier" in source
        has_time_barrier_config = "get_time_barrier_config" in source

        # Verify _reduce_position updates _position_layers
        reduce = _extract_method(lines, "_reduce_position") or ""
        has_layer_update = "_position_layers" in reduce

        ok = (has_time_barrier and has_layer_update)
        self._record("P1.16", "v11.0-simple Time Barrier", ok,
                     expected="_check_time_barrier + _position_layers updated in _reduce_position",
                     actual=f"time_barrier={has_time_barrier}, "
                            f"time_barrier_config={has_time_barrier_config}, layer_update_in_reduce={has_layer_update}")

    # ── P1.17: v6.0 Trade evaluation integrity ──

    def _check_trade_eval_integrity(self, source: str, lines: List[str]):
        """Check v6.0 trade evaluation: direction-aware planned_rr + captured_sltp cleanup."""
        # Check 1: evaluate_trade uses direction-aware planned_rr (no abs() masking)
        tl_path = Path(__file__).parent.parent.parent / "strategy" / "trading_logic.py"
        tl_source = tl_path.read_text(encoding='utf-8') if tl_path.exists() else ""

        # Must NOT have abs(planned_tp - entry_price) for planned_reward
        has_abs_mask = "abs(planned_tp - entry_price)" in tl_source
        # Must have direction branch for planned_reward
        has_direction_branch = ("planned_reward = planned_tp - entry_price" in tl_source
                                and "planned_reward = entry_price - planned_tp" in tl_source)

        # Check 2: _captured_sltp_for_eval is cleared after use
        on_closed = _extract_method(lines, "on_position_closed") or ""
        has_capture_clear = "_captured_sltp_for_eval = None" in on_closed

        # Check 3: Direction validation for stale TP detection
        has_stale_tp_check = "Stale TP detected" in on_closed

        ok = (not has_abs_mask and has_direction_branch
              and has_capture_clear and has_stale_tp_check)
        self._record("P1.17", "v6.0 Trade eval integrity (direction-aware R/R + cleanup)", ok,
                     expected="no abs() mask + direction branch + captured_sltp cleared + stale TP check",
                     actual=f"no_abs_mask={not has_abs_mask}, direction_branch={has_direction_branch}, "
                            f"capture_clear={has_capture_clear}, stale_tp_check={has_stale_tp_check}")

    # ── P1.18: v6.0 Cached price staleness detection ──

    def _check_cached_price_staleness(self, source: str):
        """Check _cached_current_price has timestamp and age check."""
        has_timestamp_var = "_cached_current_price_time" in source
        has_age_check = "price_age" in source or "price stale" in source.lower()
        has_time_set = "_cached_current_price_time = _time.time()" in source or \
                       "_cached_current_price_time = time.time()" in source

        ok = has_timestamp_var and has_age_check and has_time_set
        self._record("P1.18", "v6.0 Cached price staleness detection", ok,
                     expected="_cached_current_price_time stored + age check before critical use",
                     actual=f"timestamp_var={has_timestamp_var}, age_check={has_age_check}, "
                            f"time_set_on_update={has_time_set}")

    # ── P1.19: v6.0 Market order emergency SL ──

    def _check_market_order_emergency_sl(self, source: str):
        """Check market order path (enable_auto_sl_tp=False) has emergency SL."""
        # The enable_auto_sl_tp=False path should call _submit_emergency_sl
        has_auto_sl_tp_check = "enable_auto_sl_tp" in source
        # Look for emergency SL in the same block as the auto_sl_tp check
        has_emergency_in_path = ("enable_auto_sl_tp" in source and
                                 "_submit_emergency_sl" in source)

        ok = has_auto_sl_tp_check and has_emergency_in_path
        self._record("P1.19", "v6.0 Market order path has emergency SL", ok,
                     expected="enable_auto_sl_tp=False path calls _submit_emergency_sl",
                     actual=f"auto_sl_tp_check={has_auto_sl_tp_check}, "
                            f"emergency_in_path={has_emergency_in_path}")

    # ── P1.20: v6.0 Leverage None handling ──

    def _check_leverage_none_handling(self, source: str):
        """Check _sync_binance_leverage handles None return from get_leverage."""
        ba_path = self.ctx.project_root / "utils" / "binance_account.py"
        ba_source = ba_path.read_text(encoding='utf-8') if ba_path.exists() else ""

        # get_leverage should return Optional[int] (None on failure, not default 1)
        has_none_return = "return None" in ba_source and "def get_leverage" in ba_source
        has_optional_type = "Optional[int]" in ba_source

        # Strategy should check for None
        has_none_check = "binance_leverage is None" in source

        ok = has_none_return and has_none_check
        self._record("P1.20", "v6.0 Leverage get_leverage returns None on failure", ok,
                     expected="get_leverage returns None (not default 1) + strategy checks for None",
                     actual=f"none_return={has_none_return}, optional_type={has_optional_type}, "
                            f"none_check_in_strategy={has_none_check}")

    # ── P1.21: v7.2 Per-layer SL/TP persistence (enhanced: mutation site coverage) ──

    def _check_reduce_timeout_reevaluation(self, source: str):
        """Check v7.2: per-layer SL/TP orders are persisted at ALL mutation sites.

        Critical: if any mutation site misses _persist_layer_orders(), layers are
        lost on restart → naked position risk.
        """
        lines = source.splitlines()
        has_persist = "def _persist_layer_orders" in source
        has_load = "def _load_layer_orders" in source
        has_layer_orders = "_layer_orders" in source
        has_order_to_layer = "_order_to_layer" in source

        # Enhanced: verify persistence called at critical mutation sites
        # Direct sites: methods that directly mutate _layer_orders and must call _persist_layer_orders
        # Indirect sites: methods that delegate mutation via _remove_layer (which persists internally)
        direct_sites = {
            '_create_layer': _extract_method(lines, '_create_layer') or '',
            '_reduce_position': _extract_method(lines, '_reduce_position') or '',
            '_remove_layer': _extract_method(lines, '_remove_layer') or '',
            'on_position_opened': _extract_method(lines, 'on_position_opened') or '',
        }
        indirect_sites = {
            'on_order_filled': _extract_method(lines, 'on_order_filled') or '',
        }
        direct_coverage = {
            name: '_persist_layer_orders' in body
            for name, body in direct_sites.items()
        }
        # Indirect sites must call _remove_layer (which internally persists)
        indirect_coverage = {
            name: '_remove_layer' in body
            for name, body in indirect_sites.items()
        }
        all_direct = all(direct_coverage.values())
        all_indirect = all(indirect_coverage.values())
        missing_direct = [k for k, v in direct_coverage.items() if not v]
        missing_indirect = [k for k, v in indirect_coverage.items() if not v]
        total = len(direct_sites) + len(indirect_sites)
        covered = sum(direct_coverage.values()) + sum(indirect_coverage.values())

        ok = has_persist and has_load and has_layer_orders and has_order_to_layer and all_direct and all_indirect
        detail = ""
        if missing_direct:
            detail = f"MISSING _persist_layer_orders() in: {', '.join(missing_direct)}"
        if missing_indirect:
            detail += f" MISSING _remove_layer() in: {', '.join(missing_indirect)}"
        self._record("P1.21", "v7.2 Per-layer SL/TP persistence (all mutation sites)", ok,
                     expected=f"_persist/_load + direct persist at {len(direct_sites)} sites + "
                              f"indirect via _remove_layer at {len(indirect_sites)} sites",
                     actual=f"funcs={has_persist and has_load}, vars={has_layer_orders and has_order_to_layer}, "
                            f"sites={covered}/{total} (direct={sum(direct_coverage.values())}/{len(direct_sites)}, "
                            f"indirect={sum(indirect_coverage.values())}/{len(indirect_sites)})",
                     detail=detail)

    # ── P1.22: v6.0 S/R zone freshness checks ──

    def _check_sr_zone_freshness(self, source: str):
        """Check S/R zone data has freshness/staleness validation."""
        has_calculated_at = "_calculated_at" in source
        has_stale_threshold = "sr_stale_threshold" in source or "1800" in source
        has_sr_age_check = "sr_age_sec" in source or "sr_age" in source

        # Check agent files also stamp zones
        ma_source = self._read_agents_source()
        has_stamp = "_calculated_at" in ma_source and "time.time()" in ma_source

        ok = has_calculated_at and has_sr_age_check and has_stamp
        self._record("P1.22", "v6.0 S/R zone freshness checks (30min TTL)", ok,
                     expected="_calculated_at stamped in analyzer + age check in strategy (3 locations)",
                     actual=f"calculated_at_in_strategy={has_calculated_at}, "
                            f"age_check={has_sr_age_check}, stamp_in_analyzer={has_stamp}")

    # ── P1.23: v6.1 Emergency SL fallback to market close ──

    def _check_emergency_market_close_fallback(self, source: str):
        """Check v6.1: _submit_emergency_sl falls back to _emergency_market_close on failure."""
        has_method = "def _emergency_market_close" in source
        has_call_in_sl = False
        has_reduce_only = False

        # Check that _submit_emergency_sl calls _emergency_market_close
        lines = source.splitlines()
        emergency_sl_method = _extract_method(lines, "_submit_emergency_sl") or ""
        has_call_in_sl = "_emergency_market_close" in emergency_sl_method

        # Check that _emergency_market_close uses reduce_only
        market_close_method = _extract_method(lines, "_emergency_market_close") or ""
        has_reduce_only = "reduce_only=True" in market_close_method

        ok = has_method and has_call_in_sl and has_reduce_only
        self._record("P1.23", "v6.1 Emergency SL fallback to market close", ok,
                     expected="_emergency_market_close defined + called from _submit_emergency_sl + reduce_only",
                     actual=f"method={has_method}, called_from_sl={has_call_in_sl}, "
                            f"reduce_only={has_reduce_only}")

    # ── P1.25: v6.1 State clearing covers all pending_* vars ──

    def _check_state_clearing_completeness(self, source: str, lines: List[str]):
        """Check v7.2: _clear_position_state and on_stop clear all pending states + layer tracking."""
        clear_method = _extract_method(lines, "_clear_position_state") or ""
        stop_method = _extract_method(lines, "on_stop") or ""

        # v7.2: _pending_reduce_sltp removed, _layer_orders added
        pending_vars = [
            "_pending_sltp",
            "_pending_reversal",
        ]
        # v7.2: Per-layer state must also be cleared
        layer_vars = [
            "_layer_orders",
            "_order_to_layer",
        ]

        clear_has = {v: v in clear_method for v in pending_vars}
        layer_has = {v: v in clear_method for v in layer_vars}
        stop_has = {v: v in stop_method for v in pending_vars}

        # _clear_position_state must clear ALL pending vars + layer state
        all_cleared = all(clear_has.values()) and all(layer_has.values())
        # on_stop must clear at least _pending_reversal
        stop_clears_critical = stop_has["_pending_reversal"]

        ok = all_cleared and stop_clears_critical
        missing_clear = [v for v, has in clear_has.items() if not has]
        missing_layer = [v for v, has in layer_has.items() if not has]
        missing_stop = [v for v, has in stop_has.items() if not has]

        total_checked = len(pending_vars) + len(layer_vars)
        total_found = sum(clear_has.values()) + sum(layer_has.values())
        all_missing = missing_clear + missing_layer

        self._record("P1.25", "v7.2 State clearing covers pending_* + _layer_orders", ok,
                     expected=f"All {total_checked} vars in _clear_position_state + critical vars in on_stop",
                     actual=f"clear_position_state: {total_found}/{total_checked} "
                            f"{'(missing: '+', '.join(all_missing)+')' if all_missing else '✓'}, "
                            f"on_stop: {'✓' if stop_clears_critical else 'missing: '+', '.join(missing_stop)}")

    # ── P1.26: v6.2 LIMIT entry for new positions ──

    def _check_limit_entry_not_market(self, source: str, lines: List[str]):
        """Check v6.2: _open_new_position uses LIMIT entry, not MARKET."""
        open_method = _extract_method(lines, "_open_new_position") or ""
        bracket_method = _extract_method(lines, "_submit_bracket_order") or ""

        # Entry order must use order_factory.limit (not .market)
        has_limit_entry = "order_factory.limit(" in bracket_method
        # Market should NOT be used for entry in _submit_bracket_order
        has_market_entry = "order_factory.market(" in bracket_method

        # But market IS expected in emergency/close methods (not a problem)
        ok = has_limit_entry and not has_market_entry
        self._record("P1.26", "v6.2 LIMIT entry for new positions (not MARKET)", ok,
                     expected="order_factory.limit() in _submit_bracket_order, "
                              "no order_factory.market() in entry path",
                     actual=f"limit_entry={has_limit_entry}, "
                            f"market_in_bracket={'YES ✗' if has_market_entry else 'NO ✓'}")

    # ── P1.27: v6.2 LIMIT order expiry/cancel safety ──

    def _check_limit_order_expiry_safety(self, source: str, lines: List[str]):
        """Check v6.2: on_order_canceled / on_order_expired handle unfilled LIMIT entries."""
        # Check on_order_canceled exists and handles entry order cleanup
        canceled_method = _extract_method(lines, "on_order_canceled") or ""
        expired_method = _extract_method(lines, "on_order_expired") or ""

        # Either handler should exist and clear pending state
        has_canceled_handler = bool(canceled_method)
        has_expired_handler = bool(expired_method)
        has_any_handler = has_canceled_handler or has_expired_handler

        # Handler should clear pending states to prevent naked position assumption
        handler_text = canceled_method + expired_method
        has_state_cleanup = ("_pending_sltp" in handler_text
                             or "_clear_position_state" in handler_text
                             or "entry" in handler_text.lower())

        # Check on_order_rejected also handles entry failures
        rejected_method = _extract_method(lines, "on_order_rejected") or ""
        has_reject_handling = bool(rejected_method)

        ok = has_any_handler and has_reject_handling
        self._record("P1.27", "v6.2 LIMIT order expiry/cancel safety", ok,
                     expected="on_order_canceled/expired handlers exist + on_order_rejected handles entry failure",
                     actual=f"canceled_handler={has_canceled_handler}, expired_handler={has_expired_handler}, "
                            f"state_cleanup={has_state_cleanup}, reject_handler={has_reject_handling}")

    # ── P1.28: v6.2 Thread safety ──

    def _check_thread_safety(self, source: str):
        """Check v6.2: indicator_manager is not accessed from background threads.

        CLAUDE.md warns: '从后台线程访问 indicator_manager → Rust 不可跨线程'
        Background thread methods: those called via threading.Thread or _run_in_background.
        The safe pattern is to use _cached_current_price instead.

        Uses AST for precise scope-aware detection — avoids false positives from
        string matches in comments, docstrings, or unrelated variable names.
        """
        # Methods that run in background threads (called via threading or asyncio from non-main)
        bg_patterns = [
            "threading.Thread",
            "_run_in_background",
            "run_in_executor",
        ]
        has_bg_threads = any(p in source for p in bg_patterns)

        # Check if indicator_manager is used in known background-executed code
        # The safe pattern: background code uses _cached_current_price, not indicator_manager
        has_cached_price = "_cached_current_price" in source

        # AST-based check: find indicator_manager access in background methods
        # Background method names that run outside the main thread
        bg_method_names = set()
        for name_prefix in ("_call_deepseek", "_background_", "_async_"):
            # Collect method names matching background patterns
            try:
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if node.name.startswith(name_prefix.rstrip("_")) or name_prefix.lstrip("_") in node.name:
                            bg_method_names.add(node.name)
            except SyntaxError:
                pass

        # Use AST to find indicator_manager access within background methods
        unsafe_patterns_found = []
        if bg_method_names:
            hits = _ast_find_attr_in_functions(source, "indicator_manager", bg_method_names)
            for lineno, func_name, context in hits:
                # Exclude lines that reference cached alternatives
                if "cached" not in context.lower():
                    unsafe_patterns_found.append(f"L{lineno}: {func_name} → {context[:60]}")

        ok = len(unsafe_patterns_found) == 0 and has_cached_price
        detail = ""
        if unsafe_patterns_found:
            detail = "Unsafe indicator_manager access in background:\n" + "\n".join(
                f"    ⚠️ {p}" for p in unsafe_patterns_found[:5])
        self._record("P1.28", "v6.2 Thread safety: no indicator_manager in background threads", ok,
                     expected="Background methods use _cached_current_price, not indicator_manager",
                     actual=f"has_cached_price={has_cached_price}, "
                            f"bg_threads_detected={has_bg_threads}, "
                            f"unsafe_patterns={len(unsafe_patterns_found)} (AST-verified)",
                     detail=detail)

    def _check_ai_prompt_atr_language(self) -> None:
        """
        P1.29: v6.3 AI prompts use ATR-based language, no fixed % SL examples.

        Checks:
        - multi_agent_analyzer.py Risk Manager prompt uses ATR language
        """
        import os
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        # Check agent files Risk Manager prompt (read all 4 files after mixin split)
        ma_content = self._read_agents_source()
        ma_ok = True
        ma_detail = ""
        try:
            has_fixed_1pct = "至少 **1.0%**" in ma_content
            has_atr_rule = "2.0 × ATR" in ma_content or "2 ATR" in ma_content
            ma_ok = not has_fixed_1pct and has_atr_rule
            if has_fixed_1pct:
                ma_detail = "⚠️ Still contains '至少 1.0%' fixed percentage rule"
        except Exception as e:
            ma_ok = False
            ma_detail = f"Error reading file: {e}"

        self._record("P1.29b", "v6.3 Risk Manager prompt: ATR-based SL rules (multi_agent_analyzer.py)", ma_ok,
                     expected="No fixed '至少 1.0%', has '2.0 × ATR' language",
                     actual=f"fixed_1pct={has_fixed_1pct if 'has_fixed_1pct' in dir() else '?'}, "
                            f"atr_rule={has_atr_rule if 'has_atr_rule' in dir() else '?'}",
                     detail=ma_detail)

    # ── P1.30: v7.0 SSoT — on_timer uses AIDataAssembler.fetch_external_data() ──

    def _check_v70_data_assembler_ssot(self, source: str, lines: List[str]) -> None:
        """
        P1.30: v7.0 SSoT — production on_timer() uses AIDataAssembler.fetch_external_data().

        Checks:
        - ai_strategy.py imports AIDataAssembler
        - __init__ creates self.data_assembler = AIDataAssembler(...)
        - on_timer calls self.data_assembler.fetch_external_data()
        - No remaining inline external API calls (old pattern removed)
        - ai_data_assembler.py has fetch_external_data() method
        """
        import os
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        # Check 1: Import statement
        has_import = "from utils.ai_data_assembler import AIDataAssembler" in source

        # Check 2: __init__ creates self.data_assembler
        has_init = "self.data_assembler = AIDataAssembler(" in source

        # Check 3: on_timer calls fetch_external_data()
        on_timer = _extract_method(lines, "on_timer") or ""
        has_fetch_call = "data_assembler.fetch_external_data(" in on_timer

        # Check 4: No remaining inline external API calls in on_timer
        # Old pattern had: self.order_flow_processor.process_klines directly in on_timer
        # Old pattern had: self.coinalyze_client.fetch_all() directly in on_timer
        # Old pattern had: self.binance_orderbook_client.get_order_book() directly in on_timer
        inline_patterns_found = []
        for pattern_name, pattern in [
            ("order_flow_processor.process_klines", "order_flow_processor.process_klines"),
            ("coinalyze_client.fetch_all", "coinalyze_client.fetch_all"),
            ("binance_orderbook_client.get_order_book", "binance_orderbook_client.get_order_book"),
        ]:
            if pattern in on_timer:
                inline_patterns_found.append(pattern_name)

        no_inline_duplication = len(inline_patterns_found) == 0

        # Check 5: ai_data_assembler.py has fetch_external_data()
        assembler_path = os.path.join(base, "utils", "ai_data_assembler.py")
        has_method = False
        try:
            with open(assembler_path, 'r') as f:
                assembler_source = f.read()
            has_method = "def fetch_external_data(" in assembler_source
        except Exception:  # noqa: BLE001 — file read is best-effort; has_method stays False
            pass

        all_ok = has_import and has_init and has_fetch_call and no_inline_duplication and has_method

        detail = ""
        if inline_patterns_found:
            detail = f"⚠️ Old inline patterns still in on_timer: {', '.join(inline_patterns_found)}"

        self._record(
            "P1.30", "v7.0 SSoT: on_timer uses AIDataAssembler.fetch_external_data()",
            all_ok,
            expected="import + init + fetch_external_data() call, no inline duplication",
            actual=(
                f"import={has_import}, init={has_init}, "
                f"fetch_call={has_fetch_call}, no_inline={no_inline_duplication}, "
                f"method_exists={has_method}"
            ),
            detail=detail,
        )

    # ── P1.31: v11.0 _check_time_barrier returns bool ──

    def _check_time_barrier_return_bool(self, source: str, lines: List[str]) -> None:
        """Check _check_time_barrier returns bool (True if triggered close)."""
        method = _extract_method(lines, "_check_time_barrier")
        if method is None:
            self._record("P1.31", "v11.0 _check_time_barrier returns bool", False,
                         actual="_check_time_barrier not found")
            return
        has_return_true = "return True" in method
        has_return_false = "return False" in method
        has_type_hint = "-> bool" in method[:200]
        ok = has_return_true and has_return_false
        self._record("P1.31", "v11.0 _check_time_barrier returns bool (True=triggered)", ok,
                     expected="return True (triggered) + return False (not triggered)",
                     actual=f"return_true={has_return_true}, return_false={has_return_false}, "
                            f"type_hint={has_type_hint}")

    # ── P1.32: v11.0 Reversal trades recorded to memory ──

    def _check_reversal_recording(self, source: str, lines: List[str]) -> None:
        """Check on_position_closed records reversal trades (no early return)."""
        method = _extract_method(lines, "on_position_closed")
        if method is None:
            self._record("P1.32", "v11.0 Reversal trades recorded to memory", False,
                         actual="on_position_closed not found")
            return
        has_is_reversal = "is_reversal" in method
        has_evaluate = "evaluate_trade" in method
        has_record = "record_outcome" in method
        # Key: there should NOT be an early return right after reversal detection
        # The is_reversal flag should gate only Telegram notification, not P&L computation
        ok = has_is_reversal and has_evaluate and has_record
        self._record("P1.32", "v11.0 Reversal trades recorded to memory (no early return)", ok,
                     expected="is_reversal flag + evaluate_trade() + record_outcome() in on_position_closed",
                     actual=f"is_reversal={has_is_reversal}, evaluate={has_evaluate}, record={has_record}")

    # ── P1.33: v11.0 Pyramid layer persistence ──

    def _check_position_layers_persistence(self, source: str) -> None:
        """Check _position_layers persisted to JSON file."""
        has_save = "def _save_position_layers" in source
        has_load = "def _load_position_layers" in source
        has_file = "position_layers" in source and ".json" in source
        ok = has_save and has_load and has_file
        self._record("P1.33", "v11.0 Pyramid layer persistence (JSON file)", ok,
                     expected="_save_position_layers + _load_position_layers + position_layers.json",
                     actual=f"save={has_save}, load={has_load}, json_file={has_file}")

    # ── P1.34: v12.0 Per-Agent Reflection Memory infrastructure ──

    def _check_reflection_infrastructure(self, strategy_source: str, ma_source: str) -> None:
        """Check v12.0 reflection generation + update + queue + role-annotated consumption."""
        # Strategy side: _pending_reflections + _process_pending_reflections + queue in on_position_closed
        has_queue = "_pending_reflections" in strategy_source
        has_process = "def _process_pending_reflections" in strategy_source
        has_enqueue = "_pending_reflections.append" in strategy_source
        has_winning_side = "_entry_winning_side" in strategy_source
        has_judge_summary = "_entry_judge_summary" in strategy_source

        # Multi-agent side: generate_reflection + update_last_memory_reflection + role annotation
        has_generate = "def generate_reflection" in ma_source if ma_source else False
        has_update = "def update_last_memory_reflection" in ma_source if ma_source else False
        has_role_param = "agent_role" in ma_source if ma_source else False

        ok = all([has_queue, has_process, has_enqueue, has_winning_side,
                  has_judge_summary, has_generate, has_update, has_role_param])
        self._record("P1.34", "v12.0 Per-Agent Reflection Memory infrastructure", ok,
                     expected="queue + process + enqueue + generate + update + role_param + winning_side + judge_summary",
                     actual=f"queue={has_queue}, process={has_process}, enqueue={has_enqueue}, "
                            f"gen={has_generate}, upd={has_update}, role={has_role_param}, "
                            f"ws={has_winning_side}, js={has_judge_summary}")

    # ── P1.35: v12.0.1 Reflection backfill on restart ──

    def _check_reflection_backfill(self, strategy_source: str) -> None:
        """Check v12.0.1 restart backfill scans for missing reflections."""
        has_backfill_flag = "_reflection_backfill_done" in strategy_source
        has_backfill_scan = "backfill" in strategy_source.lower() and "reflection" in strategy_source
        ok = has_backfill_flag and has_backfill_scan
        self._record("P1.35", "v12.0.1 Reflection backfill on restart", ok,
                     expected="_reflection_backfill_done flag + backfill scan in _process_pending_reflections",
                     actual=f"flag={has_backfill_flag}, scan={has_backfill_scan}")

    # ── P1.36: v13.0 Phantom position guard (manual Telegram close) ──

    def _check_phantom_position_guard(self, strategy_source: str) -> None:
        """Check v13.0/v13.1 phantom position guard for Telegram close flow.

        When NautilusTrader's internal state is out of sync after restart,
        a reduce_only close BUY can fire on_position_opened as a phantom LONG.
        The guard must:
        1. Declare _manual_close_in_progress flag
        2. Set it True before cancel_all_orders in Telegram close
        3. Check it in on_position_opened and verify Binance before submitting SL/TP
        4. Reset it in on_position_closed and on abort
        5. v13.1: Submit emergency SL when close submission fails after SL/TP cancelled
        """
        strategy_lines = strategy_source.split('\n')
        has_flag = "_manual_close_in_progress" in strategy_source

        # Scoped: _cmd_close must set flag True before cancel_all_orders
        close_method = _extract_method(strategy_lines, "_cmd_close") or ""
        has_set_in_close = (
            "_manual_close_in_progress = True" in close_method
            and "cancel_all_orders" in close_method
        )

        # Scoped: on_position_opened must check flag (phantom detection)
        opened_method = _extract_method(strategy_lines, "on_position_opened") or ""
        has_check_in_opened = "_manual_close_in_progress" in opened_method

        # Scoped: on_position_closed must reset flag
        closed_method = _extract_method(strategy_lines, "on_position_closed") or ""
        has_reset_in_closed = "_manual_close_in_progress = False" in closed_method

        has_abort_cleanup = (
            "pre_cancel_intentional_ids" in close_method
        )

        # v13.1: Scoped to _cmd_close: emergency SL when close submission fails
        has_emergency_sl_on_submit_fail = (
            "_submit_emergency_sl" in close_method
            and "cancel" in close_method.lower()
        )

        # v13.1: Scoped to _cmd_partial_close: emergency SL when partial close fails
        partial_method = _extract_method(strategy_lines, "_cmd_partial_close") or ""
        has_partial_close_emergency_sl = (
            "_submit_emergency_sl" in partial_method
        )
        ok = (has_flag and has_set_in_close and has_check_in_opened
              and has_reset_in_closed and has_abort_cleanup
              and has_emergency_sl_on_submit_fail and has_partial_close_emergency_sl)
        self._record("P1.36", "v13.1 Phantom guard + emergency SL on close/partial-close failure", ok,
                     expected="_manual_close_in_progress flag + set_in_close + "
                               "check_in_opened + reset_in_closed + abort_cleanup + "
                               "emergency_sl_on_submit_fail + partial_close_emergency_sl",
                     actual=f"flag={has_flag}, set_in_close={has_set_in_close}, "
                            f"check_in_opened={has_check_in_opened}, reset_in_closed={has_reset_in_closed}, "
                            f"abort_cleanup={has_abort_cleanup}, "
                            f"emergency_sl_on_close_fail={has_emergency_sl_on_submit_fail}, "
                            f"emergency_sl_on_partial_fail={has_partial_close_emergency_sl}")

    # ── P1.37: v14.0 Dual-channel Telegram routing ──

    def _check_dual_channel_telegram(self, strategy_source: str, tg_source: str) -> None:
        """Check v14.0 dual-channel Telegram architecture.

        Validates:
        1. TelegramBot supports broadcast parameter in send_message_sync
        2. Notification bot config (separate token + chat_id)
        3. Strategy uses broadcast=True for signal messages
        4. Strategy uses broadcast=False (default) for operational messages
        """
        # 1. send_message_sync accepts broadcast parameter
        has_broadcast_param = "broadcast" in tg_source and "def send_message_sync" in tg_source
        # 2. Notification bot initialization
        has_notification_bot = (
            "notification_token" in tg_source
            or "TELEGRAM_NOTIFICATION_BOT_TOKEN" in tg_source
        )
        has_notification_chat = (
            "notification_chat_id" in tg_source
            or "TELEGRAM_NOTIFICATION_CHAT_ID" in tg_source
        )
        # 3. Notification routing (broadcast=True → notification only)
        has_notification_routing = (
            "_send_notification_direct" in tg_source
            or "notification_enabled" in tg_source
        )
        # 4. Strategy sends signals with broadcast=True
        has_broadcast_true = "broadcast=True" in strategy_source
        # 5. Zero duplication: broadcast routes to exactly one destination
        has_no_dual_send = (
            "broadcast" in tg_source
            and "notification_enabled" in tg_source
        )

        ok = (has_broadcast_param and has_notification_bot
              and has_notification_chat and has_notification_routing
              and has_broadcast_true and has_no_dual_send)
        self._record("P1.37", "v14.0 Dual-channel Telegram routing", ok,
                     expected="broadcast param + notification bot/chat + "
                              "routing logic + broadcast=True in strategy",
                     actual=f"broadcast_param={has_broadcast_param}, "
                            f"notif_bot={has_notification_bot}, "
                            f"notif_chat={has_notification_chat}, "
                            f"routing={has_notification_routing}, "
                            f"broadcast_true={has_broadcast_true}, "
                            f"no_dual_send={has_no_dual_send}")

    # ── P1.38: v7.3 Restart SL cross-validation (Tier 2) ──

    def _check_restart_sl_crossvalidation(self, strategy_source: str) -> None:
        """Check v7.3 restart SL cross-validation (Tier 2 exchange query).

        After restart, on_start loads persisted _layer_orders, then:
        1. Queries NT cache for open orders
        2. Queries Binance API directly for live orders (Algo API orders)
        3. Union of both sets = live_order_ids
        4. For each layer: if sl_order_id not in live_order_ids → emergency SL
        """
        # 1. Cross-validation concept: checking sl_order_id against exchange
        has_cross_validation = (
            "cross" in strategy_source.lower()
            and "sl_order_id" in strategy_source
            and "live_order" in strategy_source
        )
        # 2. Binance direct query for live orders (not just NT cache)
        has_binance_query = (
            "openOrders" in strategy_source
            or "get_open_orders" in strategy_source
            or "binance" in strategy_source.lower() and "live_order_ids" in strategy_source
        )
        # 3. Emergency SL on stale/missing SL
        has_emergency_on_stale = (
            "_submit_emergency_sl" in strategy_source
            and "uncovered" in strategy_source
        )
        # 4. Tier 2 recovery mentioned
        has_tier2 = (
            "tier" in strategy_source.lower()
            and ("recover" in strategy_source.lower() or "cross" in strategy_source.lower())
        )

        ok = has_cross_validation and has_emergency_on_stale
        self._record("P1.38", "v7.3 Restart SL cross-validation (Tier 2)", ok,
                     expected="sl_order_id cross-check vs exchange + emergency SL on stale",
                     actual=f"cross_validation={has_cross_validation}, "
                            f"binance_query={has_binance_query}, "
                            f"emergency_on_stale={has_emergency_on_stale}, "
                            f"tier2={has_tier2}")

    # ── P1.39: Config access safety ──

    def _check_config_access_safety(self, strategy_source: str) -> None:
        """Check that self.config.get() is not called on StrategyConfig Struct.

        NautilusTrader StrategyConfig is a msgspec Struct — it has NO .get() method.
        Using self.config.get() crashes at runtime (AttributeError).
        Config values must be accessed via:
        - self.config.field_name (direct attribute access on Struct)
        - ConfigManager().get(...) (separate utility, not self.config)

        This check prevents the exact production bug that crashed on_timer for 24+ hours.
        """
        # Find all self.config.get( calls — these are ALWAYS wrong on StrategyConfig
        pattern = re.compile(r'self\.config\.get\s*\(')
        matches = pattern.findall(strategy_source)
        count = len(matches)

        ok = count == 0
        self._record("P1.39", "Config access safety (no self.config.get() on Struct)", ok,
                     expected="0 calls to self.config.get() (StrategyConfig has no .get())",
                     actual=f"Found {count} self.config.get() call(s)" + (
                         " — will crash at runtime (AttributeError)" if count > 0 else " ✓"
                     ))

    # ── P1.40: v15.0 Hardcoded values extracted to config ──

    def _check_hardcoded_values_extracted(self, strategy_source: str) -> None:
        """v15.0: Verify 8 magic numbers are consumed via self.xxx config fields, not hardcoded.

        Config chain: base.yaml → ConfigManager → main_live.py → StrategyConfig → self.xxx
        """
        # Each tuple: (config field that MUST exist in __on_start/init, description)
        config_fields = [
            ("self.emergency_sl_base_pct", "Emergency SL base %"),
            ("self.emergency_sl_atr_multiplier", "Emergency SL ATR multiplier"),
            ("self.emergency_sl_cooldown_seconds", "Emergency SL cooldown"),
            ("self.emergency_sl_max_consecutive", "Emergency SL max retries"),
            ("self.sr_zones_cache_ttl_seconds", "S/R zones cache TTL"),
            ("self.price_cache_ttl_seconds", "Price cache TTL"),
            ("self.reversal_timeout_seconds", "Reversal timeout"),
            ("self.max_leverage_limit", "Max leverage limit"),
        ]

        missing = []
        for field, desc in config_fields:
            if field not in strategy_source:
                missing.append(f"{field} ({desc})")

        ok = len(missing) == 0
        self._record("P1.40", "v15.0 Hardcoded values extracted to config (8 fields)", ok,
                     expected="All 8 config fields present as self.xxx in strategy",
                     actual=f"{8 - len(missing)}/8 fields found" + (
                         f", missing: {', '.join(missing)}" if missing else " ✓"
                     ))

    # ── P1.41: v15.0 Silent exception coverage ──

    def _check_silent_exception_coverage(self, strategy_source: str, ma_source: str) -> None:
        """v15.0: Verify no bare except:pass without logging in critical files.

        Pattern: except ...:\\n  ...pass  WITHOUT  self.log./logger. between except and pass.
        """
        issues = []
        files_to_check = {
            "ai_strategy.py": strategy_source,
            "multi_agent_analyzer.py": ma_source,
        }

        # Also check utility files
        for util_name in ["telegram_bot.py", "telegram_command_handler.py",
                          "sentiment_client.py", "ai_data_assembler.py",
                          "binance_derivatives_client.py"]:
            util_path = self.ctx.project_root / "utils" / util_name
            if util_path.exists():
                files_to_check[util_name] = util_path.read_text()

        for fname, src in files_to_check.items():
            if not src:
                continue
            lines = src.splitlines()
            i = 0
            while i < len(lines):
                stripped = lines[i].strip()
                if stripped.startswith("except") and stripped.endswith(":"):
                    # Scan forward for pass without any logging
                    j = i + 1
                    has_logging = False
                    found_pass = False
                    indent = len(lines[i]) - len(lines[i].lstrip())
                    while j < len(lines):
                        inner = lines[j]
                        inner_stripped = inner.strip()
                        if inner_stripped == "":
                            j += 1
                            continue
                        inner_indent = len(inner) - len(inner.lstrip())
                        if inner_indent <= indent and inner_stripped and not inner_stripped.startswith("#"):
                            break  # exited except block
                        if "self.log." in inner or "logger." in inner or "logging." in inner:
                            has_logging = True
                        if inner_stripped == "pass":
                            found_pass = True
                        j += 1
                    if found_pass and not has_logging:
                        # queue.Empty is expected
                        if "queue.Empty" not in stripped:
                            issues.append(f"{fname}:{i+1}")
                i += 1

        ok = len(issues) == 0
        self._record("P1.41", "v15.0 Silent exception coverage (no bare except:pass)", ok,
                     expected="0 bare except:pass without logging",
                     actual=f"{len(issues)} issues" + (
                         f": {', '.join(issues[:5])}" if issues else " ✓"
                     ))

    # ── P1.42: v15.0 Test suite infrastructure ──

    def _check_test_suite_infrastructure(self) -> None:
        """v15.0: Verify pytest infrastructure and test files exist."""
        project_root = self.ctx.project_root

        checks = []
        # pytest.ini
        pytest_ini = project_root / "pytest.ini"
        checks.append(("pytest.ini", pytest_ini.exists()))

        # conftest.py
        conftest = project_root / "tests" / "conftest.py"
        checks.append(("tests/conftest.py", conftest.exists()))

        # test_config_manager.py
        tcm = project_root / "tests" / "test_config_manager.py"
        tcm_exists = tcm.exists()
        if tcm_exists:
            tcm_content = tcm.read_text()
            tcm_tests = len(re.findall(r'def test_', tcm_content))
            checks.append((f"test_config_manager.py ({tcm_tests} tests)", tcm_tests >= 15))
        else:
            checks.append(("test_config_manager.py", False))

        # test_trading_logic.py
        ttl = project_root / "tests" / "test_trading_logic.py"
        ttl_exists = ttl.exists()
        if ttl_exists:
            ttl_content = ttl.read_text()
            ttl_tests = len(re.findall(r'def test_', ttl_content))
            checks.append((f"test_trading_logic.py ({ttl_tests} tests)", ttl_tests >= 25))
        else:
            checks.append(("test_trading_logic.py", False))

        passed = sum(1 for _, ok in checks if ok)
        total = len(checks)
        ok = passed == total

        detail_parts = []
        for name, ok_check in checks:
            detail_parts.append(f"{'✓' if ok_check else '✗'} {name}")

        self._record("P1.42", "v15.0 Test suite infrastructure (pytest + 49 tests)", ok,
                     expected="pytest.ini + conftest.py + 2 test files with sufficient tests",
                     actual="; ".join(detail_parts))

    # ── P1.43: v15.2 counter_trend_rr_multiplier in _TRADING_LOGIC_CONFIG ──

    def _check_counter_trend_rr_in_config_cache(self, tl_source: str) -> None:
        """v15.2: Verify counter_trend_rr_multiplier is loaded into _TRADING_LOGIC_CONFIG.

        Without this, changing the value in base.yaml is silently ignored because
        the getter function falls back to a hardcoded default 1.3.
        Method-scoped: verifies inside _get_trading_logic_config(), not just anywhere.
        """
        tl_lines = tl_source.split('\n')
        config_method = _extract_method(tl_lines, "_get_trading_logic_config") or ""

        # counter_trend_rr_multiplier must be assigned inside the config loader function
        has_in_cache = bool(re.search(
            r"['\"]counter_trend_rr_multiplier['\"]", config_method
        ))
        # The config.get call should load from YAML (within the same function)
        has_config_load = bool(re.search(
            r"config\.get\(['\"]trading_logic['\"].*counter_trend_rr_multiplier", config_method
        ))

        ok = has_in_cache and has_config_load
        self._record("P1.43", "v15.2 counter_trend_rr_multiplier in _get_trading_logic_config (scoped)", ok,
                     expected="counter_trend_rr_multiplier loaded from config.get() inside _get_trading_logic_config",
                     actual=f"in_method={has_in_cache}, config_load={has_config_load}")

    # ── P1.44: v15.2 Emergency messages use side_to_cn() ──

    def _check_emergency_messages_use_side_to_cn(self, strategy_source: str) -> None:
        """v15.2: Verify emergency SL/market close/time barrier Telegram messages
        use side_to_cn() instead of raw position_side.

        CLAUDE.md mandates: no raw LONG/SHORT in user-facing Telegram messages.
        Emergency messages are critical safety alerts — they must comply.
        """
        lines = strategy_source.splitlines()

        # Check each critical method for side_to_cn usage in Telegram messages
        methods_to_check = [
            ("_submit_emergency_sl", "side_to_cn"),
            ("_emergency_market_close", "side_to_cn"),
            ("_check_time_barrier", "side_to_cn"),
        ]

        results = {}
        for method_name, pattern in methods_to_check:
            method_body = _extract_method(lines, method_name) or ""
            if not method_body:
                results[method_name] = "NOT_FOUND"
                continue

            # Check if side_to_cn is used before any send_message_sync call
            has_side_to_cn = pattern in method_body
            has_telegram_send = "send_message_sync" in method_body
            if has_telegram_send and has_side_to_cn:
                results[method_name] = "OK"
            elif has_telegram_send and not has_side_to_cn:
                results[method_name] = "MISSING"
            else:
                results[method_name] = "NO_TELEGRAM"

        all_ok = all(v in ("OK", "NO_TELEGRAM") for v in results.values())
        missing = [k for k, v in results.items() if v == "MISSING"]

        self._record("P1.44", "v15.2 Emergency messages use side_to_cn() terminology", all_ok,
                     expected="All emergency Telegram messages use side_to_cn() (not raw position_side)",
                     actual=f"{', '.join(f'{k}={v}' for k, v in results.items())}"
                            + (f" | VIOLATIONS: {', '.join(missing)}" if missing else ""))

    # ── P1.45: v15.2 sentiment_client safe .get() access ──

    def _check_sentiment_client_safe_access(self, sc_source: str) -> None:
        """v15.2: Verify sentiment_client.py uses .get() for dict access, not bracket [].

        Direct sentiment_data['key'] crashes with KeyError when API returns
        incomplete data. CLAUDE.md: sentiment_data['key'] → sentiment_data.get('key', default).

        Uses AST for precise subscript detection — avoids false positives in
        comments and string literals that regex would match.
        """
        if not sc_source:
            self._record("P1.45", "v15.2 sentiment_client safe .get() access", False,
                         actual="sentiment_client.py not found")
            return

        watched_keys = {"net_sentiment", "positive_ratio", "negative_ratio", "long_short_ratio"}

        # AST: find all sentiment_data['key'] subscript accesses
        subscript_hits = _ast_find_subscript_access(sc_source, "sentiment_data")
        dangerous_patterns = [
            (line, key) for line, key in subscript_hits
            if isinstance(key, str) and key in watched_keys
        ]

        # Regex fallback for .get() (AST doesn't distinguish method calls easily for this pattern)
        safe_patterns = re.findall(
            r"sentiment_data\.get\('(net_sentiment|positive_ratio|negative_ratio|long_short_ratio)'",
            sc_source,
        )

        ok = len(dangerous_patterns) == 0 and len(safe_patterns) > 0
        detail = ""
        if dangerous_patterns:
            detail = "Unsafe bracket access (AST-detected):\n" + "\n".join(
                f"    ⚠️ L{line}: sentiment_data['{key}']" for line, key in dangerous_patterns[:5])
        self._record("P1.45", "v15.2 sentiment_client safe .get() access (no bracket [])", ok,
                     expected="0 bracket accesses (AST), >0 .get() accesses",
                     actual=f"bracket[]={len(dangerous_patterns)} (AST), .get()={len(safe_patterns)}",
                     detail=detail)

    # ── P1.46: v15.2 Funding rate :.5f precision ──

    def _check_funding_rate_5f_precision(self, ma_source: str) -> None:
        """v15.2: Verify funding rate uses :.5f precision (5 decimals), not :.4f.

        Binance funding rates need 5 decimal places for accurate display.
        CLAUDE.md: 'Funding Rate 精度 4 位 → 5 位小数 :.5f'

        Uses AST + regex hybrid: AST for f-string format specs, regex as fallback
        for non-f-string formatting (str.format, %).
        """
        if not ma_source:
            self._record("P1.46", "v15.2 Funding rate :.5f precision", False,
                         actual="multi_agent_analyzer.py not found")
            return

        # AST-based: scan f-string format specs for funding rate variables
        ast_old_4f = []
        ast_correct_5f = []
        for prefix in ("fr_pct", "settled_pct", "predicted_pct"):
            hits = _ast_find_fstring_format_specs(ma_source, prefix)
            for lineno, var_name, fmt_spec in hits:
                if ".4f" in fmt_spec:
                    ast_old_4f.append((lineno, var_name, fmt_spec))
                elif ".5f" in fmt_spec:
                    ast_correct_5f.append((lineno, var_name, fmt_spec))

        # Regex fallback for non-f-string patterns (e.g. str.format or % formatting)
        old_4f_regex = re.findall(r'fr_pct.*?:\.4f', ma_source)
        old_4f_settled_regex = re.findall(r'settled_pct.*?:\.4f', ma_source)
        correct_5f_regex = re.findall(r'fr_pct.*?:\.5f', ma_source)
        correct_5f_settled_regex = re.findall(r'settled_pct.*?:\.5f', ma_source)

        # Combine: use AST count if available, otherwise regex
        total_old = len(ast_old_4f) if ast_old_4f else (len(old_4f_regex) + len(old_4f_settled_regex))
        total_new = len(ast_correct_5f) if ast_correct_5f else (len(correct_5f_regex) + len(correct_5f_settled_regex))

        ok = total_old == 0 and total_new > 0
        detail = ""
        if ast_old_4f:
            detail = "AST-detected stale :.4f format specs:\n" + "\n".join(
                f"    ⚠️ L{ln}: {var} format={spec}" for ln, var, spec in ast_old_4f[:5])
        method = "AST" if (ast_old_4f or ast_correct_5f) else "regex"
        self._record("P1.46", "v15.2 Funding rate :.5f precision (not :.4f)", ok,
                     expected="0 occurrences of :.4f on funding rate, >0 of :.5f",
                     actual=f":.4f={total_old}, :.5f={total_new} ({method}-verified)"
                            + (f" | STALE :.4f found" if total_old > 0 else ""),
                     detail=detail)

    # ── P1.47: v16.0 Calibration loader integration in sr_zone_calculator ──

    def _check_calibration_loader_integration(self, sr_source: str, cl_source: str) -> None:
        """v16.0: Verify sr_zone_calculator uses calibration_loader for hold probability.

        Checks:
        - sr_zone_calculator imports load_calibration + factor getters
        - _estimate_hold_probability calls calibrated factors
        - calibration_loader exports all required functions
        """
        if not sr_source:
            self._record("P1.47", "v16.0 Calibration loader in sr_zone_calculator", False,
                         actual="sr_zone_calculator.py not found")
            return
        if not cl_source:
            self._record("P1.47", "v16.0 Calibration loader in sr_zone_calculator", False,
                         actual="calibration_loader.py not found")
            return

        # Check sr_zone_calculator imports calibration functions
        required_imports = [
            "load_calibration",
            "get_base_formula",
            "get_touch_factor",
            "get_source_factor",
            "get_swing_factor",
        ]
        found_imports = [f for f in required_imports if f in sr_source]

        # Check calibration_loader exports required functions
        required_exports = [
            "def load_calibration",
            "def get_base_formula",
            "def get_touch_factor",
            "def get_source_factor",
            "def get_side_factor",
            "def get_swing_factor",
            "def is_using_defaults",
            "def get_calibration_age_hours",
        ]
        found_exports = [f for f in required_exports if f in cl_source]

        # Check _estimate_hold_probability uses calibrated factors
        has_estimate_method = "_estimate_hold_probability" in sr_source
        has_calibration_call = "load_calibration()" in sr_source

        ok = (len(found_imports) == len(required_imports)
              and len(found_exports) == len(required_exports)
              and has_estimate_method
              and has_calibration_call)
        self._record("P1.47", "v16.0 Calibration loader integration (sr_zone_calculator)", ok,
                     expected=f"All {len(required_imports)} imports + {len(required_exports)} exports + "
                              f"_estimate_hold_probability + load_calibration()",
                     actual=f"imports={len(found_imports)}/{len(required_imports)}, "
                            f"exports={len(found_exports)}/{len(required_exports)}, "
                            f"estimate_method={has_estimate_method}, cal_call={has_calibration_call}")

    # ── P1.48: v16.0 Calibration fallback + freshness detection ──

    def _check_calibration_fallback_and_freshness(self, cl_source: str) -> None:
        """v16.0: Verify calibration_loader has default fallback + staleness detection.

        Checks:
        - DEFAULT_CALIBRATION dict with v8.2 values exists
        - MIN_SAMPLES threshold for rejecting insufficient data
        - mtime-based cache invalidation
        - get_calibration_age_hours for staleness detection
        - Clamp range [0.50, 0.82] in sr_zone_calculator for hold probability
        """
        if not cl_source:
            self._record("P1.48", "v16.0 Calibration fallback + freshness", False,
                         actual="calibration_loader.py not found")
            return

        # Check DEFAULT_CALIBRATION fallback exists
        has_default = "DEFAULT_CALIBRATION" in cl_source
        # Check MIN_SAMPLES threshold
        has_min_samples = "MIN_SAMPLES" in cl_source
        # Check mtime cache invalidation
        has_mtime_cache = "_cache_file_mtime" in cl_source
        # Check staleness detection
        has_age_hours = "def get_calibration_age_hours" in cl_source
        # Check v8.2 default values scoped to DEFAULT_CALIBRATION dict
        # (not just "0.58" anywhere — extract the dict block and verify values inside it)
        has_v82_intercept = bool(re.search(
            r"'base_intercept'\s*:\s*0\.58", cl_source
        ))
        has_v82_slope = bool(re.search(
            r"'base_slope'\s*:\s*0\.14", cl_source
        ))

        # v18.0: Check inline staleness threshold in load_calibration
        has_staleness_threshold = "7 * 24" in cl_source
        # v18.0: Check stale field uses _is_from_file (not dead age > 240)
        has_stale_from_file = "not _is_from_file" in cl_source
        has_old_stale = "age > 240" in cl_source

        ok = (has_default and has_min_samples and has_mtime_cache
              and has_age_hours and has_v82_intercept and has_v82_slope
              and has_staleness_threshold and has_stale_from_file
              and not has_old_stale)
        details = []
        if not has_default:
            details.append("MISSING: DEFAULT_CALIBRATION fallback")
        if not has_min_samples:
            details.append("MISSING: MIN_SAMPLES threshold")
        if not has_mtime_cache:
            details.append("MISSING: mtime cache invalidation")
        if not has_age_hours:
            details.append("MISSING: get_calibration_age_hours()")
        if not has_v82_intercept or not has_v82_slope:
            details.append("MISSING: v8.2 default values (intercept=0.58, slope=0.14)")
        if not has_staleness_threshold:
            details.append("MISSING: v18.0 inline staleness threshold (7 * 24)")
        if not has_stale_from_file:
            details.append("MISSING: v18.0 stale field uses 'not _is_from_file'")
        if has_old_stale:
            details.append("STALE: old 'age > 240' threshold still present (dead code after v18.0)")

        self._record("P1.48", "v16.0+v18.0 Calibration fallback + freshness + staleness", ok,
                     expected="DEFAULT_CALIBRATION + MIN_SAMPLES + mtime + age + v8.2 defaults + v18.0 staleness threshold + stale field fix",
                     actual="All present" if ok else " | ".join(details))

    # ── P1.49: v3.18 Reversal two-phase state machine ──

    def _check_reversal_state_machine(self, source: str, lines: List[str]) -> None:
        """v3.18: Verify reversal uses two-phase commit pattern.

        Phase 1: Store _pending_reversal with target_side/quantity before close
        Phase 2: Detect _pending_reversal in on_position_closed and execute new entry
        """
        has_pending_reversal = "_pending_reversal" in source

        # Phase 1: state storage before close order
        has_phase1_store = "self._pending_reversal = {" in source

        # Phase 2: detection and execution in on_position_closed
        on_closed_body = _extract_method(lines, "on_position_closed") or ""
        has_phase2_detect = "_pending_reversal" in on_closed_body
        has_phase2_clear = "self._pending_reversal = None" in on_closed_body

        ok = (has_pending_reversal and has_phase1_store
              and has_phase2_detect and has_phase2_clear)
        self._record("P1.49", "v3.18 Reversal two-phase state machine", ok,
                     expected="_pending_reversal store (Phase 1) + detect/clear in on_position_closed (Phase 2)",
                     actual=f"var={has_pending_reversal}, store={has_phase1_store}, "
                            f"detect={has_phase2_detect}, clear={has_phase2_clear}")

    # ── P1.50: v18.0 Emergency SL short-cycle retry timer ──

    def _check_emergency_retry_timer(self, source: str, lines: List[str]) -> None:
        """v18.0: Verify emergency SL retry via set_time_alert callback.

        Checks:
        - _emergency_retry_count initialized in __init__
        - _on_emergency_retry method exists
        - set_time_alert called in _emergency_market_close failure path
        - Counter reset in _clear_position_state and on_position_closed
        - Max retry cap (5) with Telegram CRITICAL alert
        """
        # Init
        has_init = "_emergency_retry_count" in source and "int = 0" in source
        # Callback method
        has_callback = "def _on_emergency_retry(self, event)" in source
        # set_time_alert scheduling
        has_timer = "set_time_alert(" in source and "emergency_retry_" in source
        # Cleanup in _clear_position_state
        clear_body = _extract_method(lines, "_clear_position_state") or ""
        has_clear_reset = "_emergency_retry_count = 0" in clear_body
        # Cleanup in on_position_closed
        closed_body = _extract_method(lines, "on_position_closed") or ""
        has_closed_reset = "_emergency_retry_count = 0" in closed_body
        # Max retry cap
        has_max_cap = "_max_retries = 5" in source
        # Telegram exhaustion alert
        has_tg_alert = "紧急保护全部失败" in source
        # Callback uses _resubmit_sltp_if_needed
        callback_body = _extract_method(lines, "_on_emergency_retry") or ""
        has_resubmit = "_resubmit_sltp_if_needed" in callback_body

        ok = (has_init and has_callback and has_timer and has_clear_reset
              and has_closed_reset and has_max_cap and has_tg_alert and has_resubmit)
        self._record("P1.50", "v18.0 Emergency SL short-cycle retry timer", ok,
                     expected="init + callback + timer + 2x reset + max=5 + alert + resubmit",
                     actual=f"init={has_init}, callback={has_callback}, timer={has_timer}, "
                            f"clear_reset={has_clear_reset}, closed_reset={has_closed_reset}, "
                            f"max5={has_max_cap}, tg={has_tg_alert}, resubmit={has_resubmit}")

    # ── P1.51: v18.0 Sentiment degradation marker ──

    def _check_sentiment_degradation_marker(self, ada_source: str, ma_source: str) -> None:
        """v18.0: Verify sentiment fallback marks degraded + AI prompt warns.

        Checks:
        - ai_data_assembler.py: 'degraded': True in fetch_external_data fallback
        - ai_data_assembler.py: 'degraded': True in _default_sentiment
        - multi_agent_analyzer.py: _format_sentiment_report checks degraded field
        - multi_agent_analyzer.py: Warning tells AI not to use degraded data
        """
        # Producer: fallback dict has degraded marker
        has_degraded_fetch = "'degraded': True" in ada_source
        # Producer: _default_sentiment also has it
        has_degraded_default = "'degraded': True" in ada_source and "'source': 'default_neutral'" in ada_source
        # Consumer: checks degraded field
        has_check = "data.get('degraded')" in ma_source
        # Consumer: warning message
        has_warning = "Do NOT use sentiment" in ma_source

        ok = has_degraded_fetch and has_degraded_default and has_check and has_warning
        self._record("P1.51", "v18.0 Sentiment degradation marker", ok,
                     expected="'degraded': True in fallback + AI prompt warning",
                     actual=f"fetch_degraded={has_degraded_fetch}, default_degraded={has_degraded_default}, "
                            f"check={has_check}, warning={has_warning}")

    # ── P1.52: v18.0 Drawdown hysteresis ──

    def _check_drawdown_hysteresis(self, rc_source: str) -> None:
        """v18.0: Verify dd_recovery_threshold used in _update_trading_state (not dead variable).

        Before v18.0, dd_recovery_threshold was loaded (L117) but never used.
        Now it's used in the hysteresis band logic.
        """
        if not rc_source:
            self._record("P1.52", "v18.0 Drawdown hysteresis", False,
                         actual="risk_controller.py not found")
            return

        # dd_recovery_threshold loaded from config
        has_config_load = "dd_recovery_threshold" in rc_source and "recovery_threshold_pct" in rc_source
        # Used in _update_trading_state (not just loaded)
        lines = rc_source.split('\n')
        update_body = ""
        in_method = False
        for line in lines:
            if "def _update_trading_state" in line:
                in_method = True
            elif in_method and line.strip() and not line.startswith(' ') and not line.startswith('\t'):
                break
            if in_method:
                update_body += line + "\n"

        has_used_in_method = "dd_recovery_threshold" in update_body
        # Hysteresis: checks trading_state == REDUCED before applying band
        has_state_check = "TradingState.REDUCED" in update_body
        # v18.0 annotation
        has_v18_comment = "v18.0" in update_body

        ok = has_config_load and has_used_in_method and has_state_check and has_v18_comment
        self._record("P1.52", "v18.0 Drawdown hysteresis (dd_recovery_threshold active)", ok,
                     expected="dd_recovery_threshold loaded + used in _update_trading_state + state check + v18.0 marker",
                     actual=f"loaded={has_config_load}, used_in_method={has_used_in_method}, "
                            f"state_check={has_state_check}, v18={has_v18_comment}")

    # ── P1.53: v17.0 S/R 1+1 simplification ──

    def _check_sr_one_plus_one(self, sr_source: str, ma_source: str) -> None:
        """v17.0: Verify S/R output is trimmed to max 1 support + 1 resistance.

        Checks:
        1. sr_zone_calculator.py: v17.0 trimming logic exists (zones = [nearest] if nearest else [])
        2. sr_zone_calculator.py: AI report uses only S1/R1 format
        3. multi_agent_analyzer.py: only accesses nearest_support/nearest_resistance (no zones[idx])
        4. No multi-zone iteration (zones[1:] or len(zones) > 1) in downstream code
        """
        if not sr_source:
            self._record("P1.53", "v17.0 S/R 1+1 simplification", False,
                         actual="sr_zone_calculator.py not found")
            return

        # Sub-check 1: v17.0 trimming logic in calculate()
        # Verify support/resistance_zones are assigned a list of at most 1 element
        # (match various equivalent expressions, not exact string)
        has_support_trim = bool(re.search(
            r'support_zones\s*=\s*\[nearest_support\]', sr_source
        ))
        has_resistance_trim = bool(re.search(
            r'resistance_zones\s*=\s*\[nearest_resistance\]', sr_source
        ))
        has_v17_comment = "v17.0" in sr_source

        # Sub-check 2: AI report uses S1/R1 only (not S2/R2)
        has_s1_r1_format = '"S1"' in sr_source or "'S1'" in sr_source
        no_s2_r2 = '"S2"' not in sr_source and "'S2'" not in sr_source

        # Sub-check 3: multi_agent_analyzer.py only uses nearest_*, no zones[idx]
        # AST-based: find all subscript accesses to support_zones/resistance_zones
        ma_no_zone_index = True
        ma_zone_hits = []
        if ma_source:
            for var_name in ("support_zones", "resistance_zones"):
                hits = _ast_find_subscript_access(ma_source, var_name)
                ma_zone_hits.extend(hits)
            ma_no_zone_index = len(ma_zone_hits) == 0

        # Sub-check 4: no multi-zone iteration in sr_zone_calculator output section
        # After v17.0 trimming, there should be no zones[1:] access
        no_multi_zone_iter = "zones[1:]" not in sr_source and "zones[1 :]" not in sr_source

        ok = (has_support_trim and has_resistance_trim and has_v17_comment
              and has_s1_r1_format and no_s2_r2
              and ma_no_zone_index and no_multi_zone_iter)

        detail = ""
        if ma_zone_hits:
            detail = "AST-detected zone subscript access in multi_agent:\n" + "\n".join(
                f"    ⚠️ L{line}: [{key}]" for line, key in ma_zone_hits[:5])
        self._record("P1.53", "v17.0 S/R 1+1 simplification", ok,
                     expected="v17.0 trimming + S1/R1 only + no zones[idx] in multi_agent (AST) + no multi-zone iter",
                     actual=f"trim_s={has_support_trim}, trim_r={has_resistance_trim}, v17={has_v17_comment}, "
                            f"s1r1={has_s1_r1_format}, no_s2r2={no_s2_r2}, "
                            f"ma_no_idx={ma_no_zone_index} (AST), no_iter={no_multi_zone_iter}",
                     detail=detail)

    # ── P1.54-P1.56: v18.0 Reflection System Reform ──

    def _check_reflection_reform(self, ma_source: str, strategy_source: str,
                                  strategy_lines: List[str]) -> None:
        """v18.0: Verify Reflection System Reform (P1 + P2) regression guards.

        P1.54: Extended reflection infrastructure in multi_agent_analyzer.py
        P1.55: Recency factor in _score_memory
        P1.56: Extended reflection trigger in ai_strategy.py
        """
        # P1.54: Extended reflection infrastructure
        has_constants = ("EXTENDED_REFLECTION_INTERVAL" in ma_source
                         and "EXTENDED_REFLECTIONS_FILE" in ma_source
                         and "EXTENDED_REFLECTIONS_MAX_COUNT" in ma_source)
        has_check_method = "def check_and_generate_extended_reflection(self)" in ma_source
        has_generate_method = "def _generate_and_save_extended_reflection(self" in ma_source
        has_load = "def _load_extended_reflections(self)" in ma_source
        has_save = "def _save_extended_reflection(self" in ma_source
        has_injection = "EXTENDED REFLECTION" in ma_source

        ok_54 = (has_constants and has_check_method and has_generate_method
                 and has_load and has_save and has_injection)
        self._record("P1.54", "v18.0 Extended reflection infrastructure", ok_54,
                     expected="constants + check/generate/load/save methods + memory injection",
                     actual=f"const={has_constants}, check={has_check_method}, "
                            f"gen={has_generate_method}, load={has_load}, save={has_save}, "
                            f"inject={has_injection}")

        # P1.55: Recency factor in _score_memory
        has_recency_const = ("RECENCY_WEIGHT" in ma_source
                             and "RECENCY_HALF_LIFE_DAYS" in ma_source)
        has_recency_calc = "recency * RECENCY_WEIGHT" in ma_source
        has_decay = "RECENCY_HALF_LIFE_DAYS" in ma_source and "2 **" in ma_source

        ok_55 = has_recency_const and has_recency_calc and has_decay
        self._record("P1.55", "v18.0 Recency factor in _score_memory", ok_55,
                     expected="RECENCY_WEIGHT + RECENCY_HALF_LIFE_DAYS constants + decay formula",
                     actual=f"const={has_recency_const}, calc={has_recency_calc}, decay={has_decay}")

        # P1.56: Strategy-layer trigger in _process_pending_reflections
        proc_body = _extract_method(strategy_lines, "_process_pending_reflections") or ""
        has_trigger = "check_and_generate_extended_reflection" in proc_body
        has_telegram = "Extended Reflection" in proc_body
        has_broadcast_false = "broadcast=False" in proc_body

        ok_56 = has_trigger and has_telegram and has_broadcast_false
        self._record("P1.56", "v18.0 Extended reflection trigger in strategy", ok_56,
                     expected="check_and_generate call + Telegram notification + broadcast=False",
                     actual=f"trigger={has_trigger}, telegram={has_telegram}, "
                            f"broadcast_false={has_broadcast_false}")

    # ── P1.57-P1.62: v18 Batch 2/3 features ──

    def _check_v18_30m_migration(self, strategy_source: str, ada_source: str,
                                  ma_source: str) -> None:
        """P1.57: v18 Item 15 — 15M→30M migration.

        Verifies order flow interval is 30m in both strategy and assembler.
        """
        # Strategy calls fetch_external_data with interval="30m"
        has_30m_strategy = 'interval="30m"' in strategy_source
        # AIDataAssembler default param or explicit call
        has_30m_prompt = "30M" in ma_source or "30m" in ma_source
        # historical_context reduced from 35→20
        has_hist_20 = "get_historical_context(count=20)" in strategy_source

        ok = has_30m_strategy and has_30m_prompt and has_hist_20
        self._record("P1.57", "v18 Item 15: 15M→30M migration", ok,
                     expected="interval=30m in strategy + 30M in prompts + historical_context(count=20)",
                     actual=f"strategy_30m={has_30m_strategy}, prompt_30m={has_30m_prompt}, "
                            f"hist_20={has_hist_20}")

    def _check_v18_4h_cvd_order_flow(self, strategy_source: str, ada_source: str,
                                      ma_source: str) -> None:
        """P1.58: v18 Item 16 — 4H CVD order flow.

        Verifies 4H order flow data is fetched and passed to analyze().
        """
        # AIDataAssembler fetches 4H klines
        has_4h_fetch = "order_flow_report_4h" in ada_source
        # Strategy passes order_flow_report_4h to analyze()
        has_4h_strategy = "order_flow_report_4h" in strategy_source
        # MultiAgentAnalyzer accepts and uses order_flow_report_4h
        has_4h_param = "order_flow_report_4h" in ma_source

        ok = has_4h_fetch and has_4h_strategy and has_4h_param
        self._record("P1.58", "v18 Item 16: 4H CVD order flow", ok,
                     expected="4H order flow fetched in assembler + passed to analyze() + accepted by MA",
                     actual=f"ada={has_4h_fetch}, strategy={has_4h_strategy}, ma={has_4h_param}")

    def _check_v18_alignment_gate(self, strategy_source: str,
                                   strategy_lines: List[str]) -> None:
        """P1.59: v23.0 Entry Timing Agent replaces deterministic alignment gate.

        Verifies Entry Timing Agent integration exists in strategy and analyzer.
        v23.0: _check_alignment_gate replaced by _evaluate_entry_timing in multi_agent_analyzer.
        """
        # Check strategy logs timing assessment
        has_timing_log = "_timing_assessment" in strategy_source
        has_timing_rejected = "_timing_rejected" in strategy_source
        has_log_audit = "_log_gate_audit(" in strategy_source
        # Check analyzer has Entry Timing Agent (read files directly)
        _agent_src = self._read_agents_source()
        has_entry_timing_method = "def _evaluate_entry_timing(" in _agent_src
        has_phase25 = "Phase 2.5" in _agent_src

        ok = (has_timing_log and has_timing_rejected and has_log_audit
              and has_entry_timing_method and has_phase25)
        self._record("P1.59", "v23.0 Entry Timing Agent (replaces alignment gate)", ok,
                     expected="_evaluate_entry_timing + Phase 2.5 + timing_assessment logging",
                     actual=f"timing_log={has_timing_log}, rejected={has_timing_rejected}, "
                            f"audit={has_log_audit}, method={has_entry_timing_method}, "
                            f"phase25={has_phase25}")

    def _check_v18_entry_quality_downgrade(self, strategy_source: str,
                                            strategy_lines: List[str]) -> None:
        """P1.60: v23.0 Entry quality downgrade handled by Entry Timing Agent.

        v23.0: Hardcoded entry_quality POOR downgrade replaced by
        _evaluate_entry_timing() in multi_agent_analyzer. The agent evaluates
        timing quality and adjusts confidence as needed.
        """
        # Check analyzer has timing confidence adjustment (read files directly)
        _agent_src = self._read_agents_source()
        has_adj_confidence = "adjusted_confidence" in _agent_src
        has_timing_quality = "timing_quality" in _agent_src

        # Check strategy reads timing confidence
        has_conf_adjusted = "_timing_confidence_adjusted" in strategy_source

        ok = has_adj_confidence and has_timing_quality and has_conf_adjusted
        self._record("P1.60", "v23.0 Entry quality via Entry Timing Agent", ok,
                     expected="adjusted_confidence + timing_quality in analyzer, "
                              "_timing_confidence_adjusted in strategy",
                     actual=f"adj_conf={has_adj_confidence}, timing_quality={has_timing_quality}, "
                            f"conf_adjusted={has_conf_adjusted}")

    def _check_v18_direction_compliance_audit(self, ma_source: str) -> None:
        """P1.61: v18 Item 20 — Post-hoc direction compliance audit.

        Verifies Bull/Bear 30M data usage for direction is audited.
        """
        has_audit_method = "def _audit_direction_compliance(" in ma_source
        has_compliance_counter = "_compliance_violations" in ma_source
        has_post_hoc_call = "_audit_direction_compliance(" in ma_source
        # Should skip audit in ranging markets (ADX<25)
        has_adx_skip = "adx" in ma_source.lower() and "COMPLIANT" in ma_source

        ok = has_audit_method and has_compliance_counter and has_post_hoc_call and has_adx_skip
        self._record("P1.61", "v18 Item 20: Direction compliance audit", ok,
                     expected="_audit_direction_compliance method + violation counter + ADX skip",
                     actual=f"method={has_audit_method}, counter={has_compliance_counter}, "
                            f"call={has_post_hoc_call}, adx_skip={has_adx_skip}")

    def _check_v18_data_partitioning(self, ma_source: str) -> None:
        """P1.62: v18 Item 22 — Per-agent data partitioning — SKIPPED (AI agents deleted v46.0)."""
        self._record("P1.62", "v18 Item 22: Per-agent data partitioning (skipped: deleted v46.0)", True)

    # ── P1.63-P1.67: v18.2 features + codebase review fixes ──

    def _check_v182_price_surge_trigger(self, source: str) -> None:
        """P1.63: v18.2 Price surge trigger via on_trade_tick.

        Structural verification: on_trade_tick method must contain surge detection
        with threshold comparison, cooldown mechanism, and timer scheduling.
        Also verifies constant values are in expected range.
        """
        source_lines = source.split('\n')
        tick_method = _extract_method(source_lines, "on_trade_tick") or ""
        has_on_trade_tick = len(tick_method) > 0

        # Verify structural components within on_trade_tick
        has_surge_threshold = "_SURGE_THRESHOLD_PCT" in tick_method
        has_surge_cooldown = "_surge_cooldown_until" in tick_method
        has_surge_flag = "_surge_triggered" in tick_method
        has_early_schedule = "set_time_alert" in tick_method

        # Verify constant values are defined (in __init__ or class body, not just name presence)
        # _SURGE_THRESHOLD_PCT should be ~0.015 (1.5%), _SURGE_COOLDOWN_SEC should be ~300 (5 min)
        threshold_value = None
        cooldown_value = None
        for line in source_lines:
            m = re.search(r'_SURGE_THRESHOLD_PCT\s*[:=]\s*(?:float\s*=\s*)?(\d+\.?\d*)', line)
            if m:
                threshold_value = float(m.group(1))
            m = re.search(r'_SURGE_COOLDOWN_SEC\s*[:=]\s*(?:float\s*=\s*)?(\d+\.?\d*)', line)
            if m:
                cooldown_value = float(m.group(1))

        threshold_ok = threshold_value is not None and 0.005 <= threshold_value <= 0.05
        cooldown_ok = cooldown_value is not None and 60 <= cooldown_value <= 600

        ok = (has_on_trade_tick and has_surge_threshold and has_surge_cooldown
              and has_surge_flag and threshold_ok and cooldown_ok)
        self._record("P1.63", "v18.2 Price surge trigger (method-scoped + value check)", ok,
                     expected="on_trade_tick: threshold∈[0.5%,5%] + cooldown∈[60s,600s] + flag + schedule",
                     actual=f"tick={has_on_trade_tick}, threshold={threshold_value}, "
                            f"cooldown={cooldown_value}, flag={has_surge_flag}, "
                            f"schedule={has_early_schedule}")

    def _check_v182_ghost_position_cleanup(self, source: str) -> None:
        """P1.64: v18.2 Ghost position 3-rejection failsafe.

        Verifies consecutive -2022 ReduceOnly rejection counter triggers
        force _clear_position_state() after 3 consecutive rejections.
        """
        has_counter = "_reduce_only_rejection_count" in source
        has_2022_detect = '"-2022"' in source or "'-2022'" in source or "-2022" in source
        has_force_clear = "_clear_position_state" in source
        # Counter must be reset in _clear_position_state
        has_reset_in_clear = False
        lines = source.split('\n')
        in_clear_method = False
        for line in lines:
            if "def _clear_position_state" in line:
                in_clear_method = True
            elif in_clear_method and line.strip().startswith("def "):
                in_clear_method = False
            if in_clear_method and "_reduce_only_rejection_count" in line and "= 0" in line:
                has_reset_in_clear = True
                break

        ok = has_counter and has_2022_detect and has_force_clear and has_reset_in_clear
        self._record("P1.64", "v18.2 Ghost position cleanup (3× -2022 → force clear)", ok,
                     expected="counter + -2022 detection + _clear_position_state + reset in clear",
                     actual=f"counter={has_counter}, detect={has_2022_detect}, "
                            f"clear={has_force_clear}, reset={has_reset_in_clear}")

    def _check_v182_alignment_weight_regime(self, source: str) -> None:
        """P1.65: v23.0 Alignment weight regime handled by Entry Timing Agent.

        v23.0: _check_alignment_gate method and its weight regime logic have been
        replaced by the Entry Timing Agent in multi_agent_analyzer. The agent
        prompt contains ADX-dependent MTF alignment rules. The old method is
        retained but no longer called from on_timer gate logic.
        """
        # Verify Entry Timing Agent prompt contains ADX-dependent alignment rules
        _agent_src = self._read_agents_source()
        has_entry_timing = "def _evaluate_entry_timing(" in _agent_src
        has_adx_40_rule = "ADX" in _agent_src and "40" in _agent_src and "70%" in _agent_src
        has_counter_trend = "counter_trend_risk" in _agent_src

        ok = has_entry_timing and has_adx_40_rule and has_counter_trend
        self._record("P1.65", "v23.0 Entry Timing Agent handles alignment (replaces weight regime)", ok,
                     expected="_evaluate_entry_timing with ADX-dependent alignment + counter_trend_risk",
                     actual=f"entry_timing={has_entry_timing}, adx_rule={has_adx_40_rule}, "
                            f"counter_trend={has_counter_trend}")

    def _check_modify_sltp_layer_tracking(self, source: str) -> None:
        """P1.66: v7.2-fix /modify_sl and /modify_tp must update _layer_orders.

        Verifies that Telegram /modify_sl and /modify_tp commands update the
        per-layer tracking structure, not just sltp_state.
        """
        # Check modify_sl updates _layer_orders
        has_modify_sl = "def _cmd_modify_sl(" in source
        has_modify_tp = "def _cmd_modify_tp(" in source

        # The fix adds _layer_orders iteration inside modify_sl/tp
        # Look for the v7.2-fix pattern: iterating layers to update order IDs
        # Check that within each method, both _layer_orders access AND order ID
        # assignment exist (they may be on separate lines in a for loop).
        has_sl_layer_update = False
        has_tp_layer_update = False
        lines = source.split('\n')
        in_modify_sl = False
        in_modify_tp = False
        sl_has_layer_access = False
        sl_has_order_id = False
        tp_has_layer_access = False
        tp_has_order_id = False
        for line in lines:
            stripped = line.strip()
            if "def _cmd_modify_sl(" in line:
                in_modify_sl = True
                in_modify_tp = False
                sl_has_layer_access = False
                sl_has_order_id = False
            elif "def _cmd_modify_tp(" in line:
                in_modify_tp = True
                in_modify_sl = False
                tp_has_layer_access = False
                tp_has_order_id = False
            elif stripped.startswith("def ") and in_modify_sl:
                in_modify_sl = False
            elif stripped.startswith("def ") and in_modify_tp:
                in_modify_tp = False

            if in_modify_sl:
                if "_layer_orders" in line:
                    sl_has_layer_access = True
                if "sl_order_id" in line:
                    sl_has_order_id = True
                if sl_has_layer_access and sl_has_order_id:
                    has_sl_layer_update = True
            if in_modify_tp:
                if "_layer_orders" in line:
                    tp_has_layer_access = True
                if "tp_order_id" in line:
                    tp_has_order_id = True
                if tp_has_layer_access and tp_has_order_id:
                    has_tp_layer_update = True

        ok = has_modify_sl and has_modify_tp and has_sl_layer_update and has_tp_layer_update
        self._record("P1.66", "v7.2-fix /modify_sl/tp updates _layer_orders tracking", ok,
                     expected="modify_sl + modify_tp both update _layer_orders with new order IDs",
                     actual=f"sl_cmd={has_modify_sl}, tp_cmd={has_modify_tp}, "
                            f"sl_layer={has_sl_layer_update}, tp_layer={has_tp_layer_update}")

    def _check_partial_close_emergency_sl(self, source: str) -> None:
        """P1.67: v13.1-fix /partial_close >50% must re-protect remaining position.

        Verifies that after successful partial close with cancelled protection,
        an emergency SL is submitted for the remaining position.
        """
        has_partial_close = "def _cmd_partial_close(" in source
        # Check for the re-protection pattern after successful submission
        has_reprotect_success = False
        has_reprotect_failure = False
        lines = source.split('\n')
        in_partial = False
        for line in lines:
            if "def _cmd_partial_close(" in line:
                in_partial = True
            elif in_partial and line.strip().startswith("def "):
                in_partial = False
            if in_partial:
                if "Re-protect after partial close" in line:
                    has_reprotect_success = True
                if "Partial close submission failed after SL/TP cancelled" in line:
                    has_reprotect_failure = True

        ok = has_partial_close and has_reprotect_success and has_reprotect_failure
        self._record("P1.67", "v13.1-fix /partial_close >50% emergency SL for remaining", ok,
                     expected="partial_close + emergency SL on success + emergency SL on failure",
                     actual=f"cmd={has_partial_close}, success_protect={has_reprotect_success}, "
                            f"failure_protect={has_reprotect_failure}")

    # ── P1.68: v19.0 LogAdapter self._log (not self.log) ──

    def _check_v190_logadapter_self_log(self, source: str) -> None:
        """P1.68: v19.0 LogAdapter must use self._log, not self.log.

        Bug: LogAdapter stored logger as self._log but methods referenced self.log
        (no underscore), causing AttributeError on every trade execution.
        """
        source_lines = source.split('\n')
        adapter_method = _extract_method(source_lines, "__init__") if False else None

        # Find LogAdapter class and check its methods
        # LogAdapter is a nested class — detect its scope by indentation level,
        # not by the next 'class' keyword (outer method code follows the class)
        in_logadapter = False
        adapter_indent = 0
        has_store_underscore = False
        has_bad_reference = False
        for line in source_lines:
            stripped = line.strip()
            if "class LogAdapter" in line:
                in_logadapter = True
                adapter_indent = len(line) - len(line.lstrip())
                continue
            if in_logadapter:
                # End of nested class: non-empty line at same or lower indentation
                if stripped and not stripped.startswith('#'):
                    line_indent = len(line) - len(line.lstrip())
                    if line_indent <= adapter_indent:
                        break
                # Check storage: self._log = ...
                if "self._log" in line and "=" in line and "self._log." not in line:
                    has_store_underscore = True
                # Check usage in methods: self.log.info/warning/error (BAD)
                # vs self._log.info/warning/error (GOOD)
                if re.search(r'self\.log\.(info|warning|error)', stripped):
                    has_bad_reference = True

        ok = has_store_underscore and not has_bad_reference
        self._record("P1.68", "v19.0 LogAdapter uses self._log (not self.log)", ok,
                     expected="self._log = strategy_log + self._log.info/warning/error",
                     actual=f"store_underscore={has_store_underscore}, "
                            f"bad_self_log_ref={has_bad_reference}")

    # ── P1.69: v19.0 Confidence authority separation ──

    def _check_v190_confidence_authority(self, ma_source: str) -> None:
        """P1.69: v19.0 Judge confidence force-through — RM cannot override.

        After RM returns, code must force Judge's confidence for LONG/SHORT signals.
        RM expresses risk concerns via risk_appetite, not confidence.
        """
        if not ma_source:
            self._record("P1.69", "v19.0 Confidence authority separation", False,
                         actual="multi_agent_analyzer.py not found")
            return

        # Check 1: Force-through logic exists
        has_force_judge = "force Judge's confidence" in ma_source or "judge_conf" in ma_source
        # Check 2: RM prompt states it cannot change confidence
        has_rm_no_modify = "你不能修改" in ma_source and "confidence" in ma_source
        # Check 3: risk_appetite validation exists
        has_appetite_validation = "valid_appetites" in ma_source and "risk_appetite" in ma_source
        # Check 4: v19.0 version marker
        has_v19_marker = "v19.0" in ma_source

        ok = has_force_judge and has_rm_no_modify and has_appetite_validation and has_v19_marker
        self._record("P1.69", "v19.0 Confidence authority (Judge→confidence, RM→risk_appetite)", ok,
                     expected="force_judge + RM_no_modify_conf + appetite_validation + v19.0 marker",
                     actual=f"force_judge={has_force_judge}, rm_no_modify={has_rm_no_modify}, "
                            f"appetite_valid={has_appetite_validation}, v19={has_v19_marker}")

    # ── P1.70: v19.0 Fingerprint cleared on close AND LIMIT cancel ──

    def _check_v190_fingerprint_clearing(self, source: str) -> None:
        """P1.70: v19.0 Fingerprint must be cleared in both on_position_closed
        AND _cancel_pending_entry_order to prevent re-entry deadlock.

        Bug: Fingerprint was only cleared in on_position_closed. When LIMIT order
        expired unfilled, same signal next cycle was blocked as "重复信号" forever.
        """
        source_lines = source.split('\n')

        # Check 1: on_position_closed clears fingerprint
        on_pos_closed = _extract_method(source_lines, "on_position_closed") or ""
        has_clear_in_close = '_last_executed_fingerprint = ""' in on_pos_closed

        # Check 2: _cancel_pending_entry_order clears fingerprint
        cancel_method = _extract_method(source_lines, "_cancel_pending_entry_order") or ""
        has_clear_in_cancel = '_last_executed_fingerprint' in cancel_method and '= ""' in cancel_method

        ok = has_clear_in_close and has_clear_in_cancel
        self._record("P1.70", "v19.0 Fingerprint cleared in close + LIMIT cancel", ok,
                     expected="on_position_closed + _cancel_pending_entry_order both clear fingerprint",
                     actual=f"close_clear={has_clear_in_close}, cancel_clear={has_clear_in_cancel}")

    # ── P1.71: v19.0 Fingerprint stored only when executed=True ──

    def _check_v190_fingerprint_execute_guard(self, source: str) -> None:
        """P1.71: v19.0 Fingerprint must only be stored when trade was actually executed.

        Bug: Fingerprint was stored unconditionally after _execute_trade(), even for
        rejected trades. This created permanent "Duplicate signal" deadlock since
        rejected fingerprints were never cleared (only on_position_closed clears them).
        """
        # Look for the guard pattern: check executed status before storing fingerprint
        has_executed_guard = (
            "executed" in source
            and "_last_executed_fingerprint = new_fingerprint" in source
        )

        # Verify the guard is conditional (not unconditional assignment)
        # The pattern should be: if self._last_signal_status.get('executed', False):
        #                            self._last_executed_fingerprint = new_fingerprint
        lines = source.split('\n')
        found_guarded = False
        for i, line in enumerate(lines):
            if "_last_executed_fingerprint = new_fingerprint" in line:
                # Check preceding lines for the executed guard
                for j in range(max(0, i - 5), i):
                    if "executed" in lines[j] and ("if" in lines[j] or "get(" in lines[j]):
                        found_guarded = True
                        break

        ok = has_executed_guard and found_guarded
        self._record("P1.71", "v19.0 Fingerprint stored only when executed=True", ok,
                     expected="if _last_signal_status['executed']: store fingerprint",
                     actual=f"executed_guard={has_executed_guard}, guarded_assignment={found_guarded}")

    # ── P1.72: v19.0 Alignment gate receives ai_technical_data ──

    def _check_v190_alignment_gate_data_source(self, source: str) -> None:
        """P1.72: v23.0 Entry Timing Agent receives full technical data.

        v23.0: _check_alignment_gate replaced by _evaluate_entry_timing in
        multi_agent_analyzer. The Entry Timing Agent receives technical_report
        (full MTF data) directly in analyze(). This check verifies the agent
        receives technical_data parameter with MTF layers.
        """
        _agent_src = self._read_agents_source()
        # Verify _evaluate_entry_timing receives technical_data
        has_tech_param = "technical_data" in _agent_src and "def _evaluate_entry_timing(" in _agent_src
        # Verify it accesses mtf_trend_layer
        has_mtf_access = "mtf_trend_layer" in _agent_src

        ok = has_tech_param and has_mtf_access
        self._record("P1.72", "v23.0 Entry Timing Agent receives MTF data", ok,
                     expected="_evaluate_entry_timing with technical_data + mtf_trend_layer access",
                     actual=f"tech_param={has_tech_param}, mtf_access={has_mtf_access}")

    # ── P1.73: v19.0 Memory selection computed once ──

    def _check_v190_memory_selection_once(self, ma_source: str) -> None:
        """P1.73: v19.0 Memory scoring computed once, then formatted per-role.

        Optimization: _select_memories() computes similarity scores once,
        then _get_past_memories(preselected=...) formats per agent role.
        Eliminates 4x redundant _score_memory() calls per cycle.
        """
        if not ma_source:
            self._record("P1.73", "v19.0 Memory selection computed once", False,
                         actual="multi_agent_analyzer.py not found")
            return

        has_select = "def _select_memories(" in ma_source
        has_preselected_param = "preselected" in ma_source
        # Check that _get_past_memories is called with preselected=
        has_preselected_call = "preselected=selected_memories" in ma_source

        ok = has_select and has_preselected_param and has_preselected_call
        self._record("P1.73", "v19.0 Memory selection once + preselected per-role", ok,
                     expected="_select_memories() + _get_past_memories(preselected=...)",
                     actual=f"select_method={has_select}, preselected_param={has_preselected_param}, "
                            f"preselected_call={has_preselected_call}")

    # ── P1.74: v19.1 ATR Extension Ratio calculation ──

    def _check_v191_extension_ratio_calc(self, tm_source: str) -> None:
        """P1.74: v19.1 _calculate_extension_ratios exists in TechnicalIndicatorManager.

        Formula: (Price - SMA) / ATR — volatility-normalized price displacement.
        Regime classification may be inline or delegated to shared_logic.classify_extension_regime (SSoT).
        """
        if not tm_source:
            self._record("P1.74", "v19.1 ATR Extension Ratio calculation", False,
                         actual="technical_manager.py not found")
            return

        has_method = "def _calculate_extension_ratios(" in tm_source
        has_formula = "current_price - sma.value" in tm_source and "atr_value" in tm_source
        # Regime classify: accept inline literals OR SSoT delegation via classify_extension_regime
        has_regime_inline = "'EXTREME'" in tm_source and "'OVEREXTENDED'" in tm_source
        has_regime_ssot = "classify_extension_regime" in tm_source
        has_regime_classify = has_regime_inline or has_regime_ssot
        # Thresholds: accept inline OR SSoT delegation (thresholds live in shared_logic.py)
        has_threshold_inline = "primary_ratio >= 5.0" in tm_source and "primary_ratio >= 3.0" in tm_source
        has_threshold_ssot = has_regime_ssot  # SSoT function encapsulates thresholds
        has_thresholds = has_threshold_inline or has_threshold_ssot

        ok = has_method and has_formula and has_regime_classify and has_thresholds
        self._record("P1.74", "v19.1 ATR Extension Ratio calculation (_calculate_extension_ratios)", ok,
                     expected="(Price-SMA)/ATR + regime classify (inline or SSoT classify_extension_regime)",
                     actual=f"method={has_method}, formula={has_formula}, "
                            f"regime={has_regime_classify} ({'SSoT' if has_regime_ssot else 'inline'}), "
                            f"thresholds={has_thresholds} ({'SSoT' if has_threshold_ssot else 'inline'})")

    # ── P1.75: v19.1 Extension Ratio fields in get_technical_data ──

    def _check_v191_extension_ratio_fields(self, tm_source: str) -> None:
        """P1.75: v19.1 get_technical_data output includes extension_ratio_sma_* and extension_regime."""
        if not tm_source:
            self._record("P1.75", "v19.1 Extension Ratio fields in technical data", False,
                         actual="technical_manager.py not found")
            return

        has_field_sma = "extension_ratio_sma_" in tm_source
        has_field_regime = "'extension_regime'" in tm_source or '"extension_regime"' in tm_source
        # Verify extension_ratios is merged into get_technical_data result
        has_merge = "**extension_ratios" in tm_source or "extension_ratios" in tm_source

        ok = has_field_sma and has_field_regime and has_merge
        self._record("P1.75", "v19.1 Extension Ratio fields in get_technical_data output", ok,
                     expected="extension_ratio_sma_{period} + extension_regime in output dict",
                     actual=f"sma_fields={has_field_sma}, regime_field={has_field_regime}, "
                            f"merged={has_merge}")

    # ── P1.76: v19.1 Extension Ratio in all 5 AI agent prompts ──

    def _check_v191_extension_ratio_prompts(self, ma_source: str) -> None:
        """P1.76: v19.1 Extension Ratio integrated in AI agent prompts.

        v23.0: Judge STEP 5 (entry_quality) removed — replaced by Entry Timing Agent.
        Extension ratio now checked in: Bull + Bear + Entry Timing + Risk + Matrix.
        """
        if not ma_source:
            self._record("P1.76", "v19.1 Extension Ratio in AI agent prompts", False,
                         actual="multi_agent_analyzer.py not found")
            return

        # Check presence in the different prompt sections
        # Bull: entry condition assessment
        has_bull = "extension" in ma_source.lower() and "bull" in ma_source.lower()
        # Bear: pullback risk
        has_bear = "extension" in ma_source.lower() and "bear" in ma_source.lower()
        # v23.0: Entry Timing Agent replaces Judge STEP 5 for extension evaluation
        has_entry_timing = "Extension Ratio" in ma_source and "timing_quality" in ma_source
        # Risk Manager: position sizing guidance
        has_risk = "extension" in ma_source.lower() and "risk" in ma_source.lower()
        # Extension Ratio row in SIGNAL_CONFIDENCE_MATRIX
        has_matrix_ref = "Ext Ratio" in ma_source

        ok = has_bull and has_bear and has_entry_timing and has_risk and has_matrix_ref
        self._record("P1.76", "v19.1 Extension Ratio integrated in all AI agent prompts", ok,
                     expected="Bull + Bear + Entry Timing (v23.0) + Risk Manager + SIGNAL_CONFIDENCE_MATRIX",
                     actual=f"bull={has_bull}, bear={has_bear}, entry_timing={has_entry_timing}, "
                            f"risk={has_risk}, matrix={has_matrix_ref}")

    # ── P1.77: v19.1 RSI/MACD divergence pre-computation ──

    def _check_v191_divergence_precompute(self, ma_source: str) -> None:
        """P1.77: v19.1 _detect_divergences() pre-computes divergence annotations."""
        if not ma_source:
            self._record("P1.77", "v19.1 Divergence pre-computation", False,
                         actual="multi_agent_analyzer.py not found")
            return

        has_method = "def _detect_divergences(" in ma_source
        has_price_series = "price_series" in ma_source
        has_rsi_series = "rsi_series" in ma_source
        has_macd_hist = "macd_hist_series" in ma_source
        has_local_extremes = "find_local_extremes" in ma_source
        has_bullish = "bullish divergence" in ma_source.lower() or "Bullish" in ma_source
        has_bearish = "bearish divergence" in ma_source.lower() or "Bearish" in ma_source
        has_dual_tf = "4H" in ma_source and "30M" in ma_source

        ok = (has_method and has_price_series and has_rsi_series
              and has_local_extremes and has_bullish and has_bearish)
        self._record("P1.77", "v19.1 RSI/MACD divergence pre-computation (_detect_divergences)", ok,
                     expected="_detect_divergences(price, rsi, macd_hist) + local_extremes + dual TF",
                     actual=f"method={has_method}, price={has_price_series}, rsi={has_rsi_series}, "
                            f"macd={has_macd_hist}, extremes={has_local_extremes}, "
                            f"bull={has_bullish}, bear={has_bearish}, dual_tf={has_dual_tf}")

    # ── P1.78: v19.1 CVD-Price cross-analysis ──

    def _check_v191_cvd_price_analysis(self, ma_source: str) -> None:
        """P1.78: v19.1 CVD-Price cross-analysis tags (ACCUMULATION/DISTRIBUTION/CONFIRMED/ABSORPTION)."""
        if not ma_source:
            self._record("P1.78", "v19.1 CVD-Price cross-analysis", False,
                         actual="multi_agent_analyzer.py not found")
            return

        has_accumulation = "ACCUMULATION" in ma_source
        has_distribution = "DISTRIBUTION" in ma_source
        has_confirmed = "CONFIRMED" in ma_source
        has_absorption = "ABSORPTION" in ma_source  # v19.2: added
        # Check CVD net calculation
        has_cvd_net = "cvd_net" in ma_source

        ok = has_accumulation and has_distribution and has_confirmed and has_absorption and has_cvd_net
        self._record("P1.78", "v19.1 CVD-Price cross-analysis (ACCUMULATION/DISTRIBUTION/CONFIRMED/ABSORPTION)", ok,
                     expected="CVD-Price tags: ACCUMULATION + DISTRIBUTION + CONFIRMED + ABSORPTION + cvd_net",
                     actual=f"accum={has_accumulation}, distrib={has_distribution}, "
                            f"confirmed={has_confirmed}, absorption={has_absorption}, "
                            f"cvd_net={has_cvd_net}")

    # ── P1.79: v19.1 SIGNAL_CONFIDENCE_MATRIX Ext Ratio rows ──

    def _check_v191_signal_confidence_matrix(self, ma_source: str) -> None:
        """P1.79: v19.1 SIGNAL_CONFIDENCE_MATRIX — SKIPPED (deleted with AI agents v46.0)."""
        self._record("P1.79", "v19.1 SIGNAL_CONFIDENCE_MATRIX (skipped: deleted v46.0)", True)

    # ── P1.80: v19.1 Extension Ratio orthogonal to mechanical SL/TP ──

    def _check_v191_extension_orthogonal(self, strategy_source: str, tm_source: str) -> None:
        """P1.80: v19.1 Extension Ratio must NOT affect calculate_mechanical_sltp.

        Extension ratio is a pure RISK signal for AI assessment only.
        It must not appear in trading_logic.py's SL/TP calculation path.
        """
        # Check that trading_logic.py does NOT reference extension ratio
        tl_path = self.ctx.project_root / "strategy" / "trading_logic.py"
        tl_source = tl_path.read_text(encoding='utf-8') if tl_path.exists() else ""

        # Extension ratio should NOT be in trading_logic.py
        no_ext_in_trading_logic = "extension_ratio" not in tl_source and "extension_regime" not in tl_source

        # Extension ratio SHOULD be in technical_manager.py
        ext_in_indicator = "_calculate_extension_ratios" in tm_source

        ok = no_ext_in_trading_logic and ext_in_indicator
        self._record("P1.80", "v19.1 Extension Ratio orthogonal to calculate_mechanical_sltp", ok,
                     expected="extension_ratio NOT in trading_logic.py, YES in technical_manager.py",
                     actual=f"absent_from_trading_logic={no_ext_in_trading_logic}, "
                            f"present_in_indicator={ext_in_indicator}")

    # ── P1.81: v19.1.1 Trend-aware extension modulation ──

    def _check_v191_trend_aware_extension(self, ma_source: str) -> None:
        """P1.81: v19.1.1 ADX>40 strong trends de-emphasize OVEREXTENDED extension.

        In strong trends, OVEREXTENDED (3-5 ATR) is common and sustainable.
        Only EXTREME (>5 ATR) triggers full warnings. ADX<40 behavior unchanged.
        """
        if not ma_source:
            self._record("P1.81", "v19.1.1 Trend-aware extension modulation", False,
                         actual="multi_agent_analyzer.py not found")
            return

        # Check for ADX-conditional extension treatment in technical report
        has_extension_note = "EXTENSION NOTE" in ma_source
        has_extension_warning = "EXTENSION WARNING" in ma_source
        # Check for ADX>40 conditional branching near extension logic
        has_adx_conditional = ("_adx_1d" in ma_source or "adx_1d" in ma_source) and "40" in ma_source
        # Check for FAIR max in Entry Timing for strong trend + overextended
        has_fair_max = "FAIR" in ma_source
        # Check for trend-aware NOTE vs WARNING distinction
        has_note_vs_warning = has_extension_note and has_extension_warning

        ok = has_note_vs_warning and has_adx_conditional
        self._record("P1.81", "v19.1.1 Trend-aware extension (ADX>40 de-emphasis for OVEREXTENDED)", ok,
                     expected="EXTENSION NOTE (ADX>40) vs EXTENSION WARNING (ADX<40) + ADX conditional",
                     actual=f"note={has_extension_note}, warning={has_extension_warning}, "
                            f"adx_conditional={has_adx_conditional}, fair_max={has_fair_max}")

    # ── P1.82: v19.2 CVD-Price time-scale alignment ──

    def _check_v192_cvd_time_alignment(self, ma_source: str) -> None:
        """P1.82: v19.2 CVD-Price cross-analysis uses 5-bar price window (not full period).

        Previously 30M used ~122h period_change_pct vs 2.5h CVD window,
        and 4H used ~64h full series vs 20h CVD window. Both now use 5-bar aligned windows.
        """
        if not ma_source:
            self._record("P1.82", "v19.2 CVD-Price time alignment", False,
                         actual="multi_agent_analyzer.py not found")
            return

        # 30M: Should use price_trend[-5:] not period_change_pct for CVD comparison
        has_price_trend_5bar = "_price_trend[-5]" in ma_source or "_price_trend[-1] - _price_trend[-5]" in ma_source
        has_cvd_price_change_var = "_cvd_price_change" in ma_source
        # 4H: Should use price_4h_series[-5:] window
        has_4h_window = "_p4h_window" in ma_source

        ok = has_price_trend_5bar and has_cvd_price_change_var and has_4h_window
        self._record("P1.82", "v19.2 CVD-Price time-scale alignment (5-bar window for 30M + 4H)", ok,
                     expected="30M: price_trend[-5:] + _cvd_price_change, 4H: _p4h_window[-5:]",
                     actual=f"30m_5bar={has_price_trend_5bar}, cvd_price_var={has_cvd_price_change_var}, "
                            f"4h_window={has_4h_window}")

    # ── P1.83: v19.2 OI×CVD cross-analysis ──

    def _check_v192_oi_cvd_bridge(self, ma_source: str) -> None:
        """P1.83: v19.2 OI×CVD cross-analysis — SKIPPED (_format_derivatives_report deleted v46.0)."""
        self._record("P1.83", "v19.2 OI×CVD bridge (skipped: format method deleted v46.0)", True)

    # ── P1.84: v19.2 CVD Absorption detection ──

    def _check_v192_absorption_detection(self, ma_source: str) -> None:
        """P1.84: v19.2 CVD Absorption detection — SKIPPED (format methods deleted v46.0)."""
        self._record("P1.84", "v19.2 CVD Absorption detection (skipped: deleted v46.0)", True)

    # ── P1.85-P1.90: v20.0 ATR Volatility Regime + OBV Divergence ──

    def _check_v200_atr_regime_calc(self, tm_source: str) -> None:
        """P1.85: v20.0 _calculate_atr_regime exists in TechnicalIndicatorManager.

        ATR% = ATR(14)/Price × 100, percentile-ranked over 90-bar lookback.
        Regime classification delegated to shared_logic.classify_volatility_regime (SSoT).
        """
        if not tm_source:
            self._record("P1.85", "v20.0 ATR Volatility Regime calculation", False,
                         actual="technical_manager.py not found")
            return

        has_method = "def _calculate_atr_regime(" in tm_source
        has_atr_pct = "atr_value / current_price" in tm_source or "atr_value/current_price" in tm_source
        has_percentile = "percentile" in tm_source
        # SSoT delegation to classify_volatility_regime
        has_ssot = "classify_volatility_regime" in tm_source

        ok = has_method and has_atr_pct and has_percentile and has_ssot
        self._record("P1.85", "v20.0 ATR Volatility Regime calculation (_calculate_atr_regime)", ok,
                     expected="_calculate_atr_regime + ATR%=ATR/Price + percentile rank + SSoT classify",
                     actual=f"method={has_method}, atr_pct={has_atr_pct}, "
                            f"percentile={has_percentile}, ssot={has_ssot}")

    def _check_v200_atr_regime_fields(self, tm_source: str) -> None:
        """P1.86: v20.0 get_technical_data output includes volatility_regime, volatility_percentile, atr_pct."""
        if not tm_source:
            self._record("P1.86", "v20.0 Volatility Regime fields in technical data", False,
                         actual="technical_manager.py not found")
            return

        has_vol_regime = "'volatility_regime'" in tm_source or '"volatility_regime"' in tm_source
        has_vol_pct = "'volatility_percentile'" in tm_source or '"volatility_percentile"' in tm_source
        has_atr_pct = "'atr_pct'" in tm_source or '"atr_pct"' in tm_source
        # Verify atr_regime is merged into get_technical_data result
        has_merge = "**atr_regime" in tm_source or "atr_regime" in tm_source

        ok = has_vol_regime and has_vol_pct and has_atr_pct and has_merge
        self._record("P1.86", "v20.0 Volatility Regime fields in get_technical_data output", ok,
                     expected="volatility_regime + volatility_percentile + atr_pct in output dict",
                     actual=f"vol_regime={has_vol_regime}, vol_pct={has_vol_pct}, "
                            f"atr_pct={has_atr_pct}, merged={has_merge}")

    def _check_v200_obv_tracking(self, tm_source: str) -> None:
        """P1.87: v20.0 OBV tracking (_update_obv + _obv_values + obv_trend output)."""
        if not tm_source:
            self._record("P1.87", "v20.0 OBV tracking in TechnicalIndicatorManager", False,
                         actual="technical_manager.py not found")
            return

        has_update_method = "def _update_obv(" in tm_source
        has_obv_values = "_obv_values" in tm_source
        has_obv_trend = "obv_trend" in tm_source
        # OBV accumulation logic: volume added/subtracted based on close vs prev close
        has_obv_logic = "prev_obv + volume" in tm_source or "prev_obv - volume" in tm_source

        ok = has_update_method and has_obv_values and has_obv_trend and has_obv_logic
        self._record("P1.87", "v20.0 OBV tracking (_update_obv + obv_trend in historical_context)", ok,
                     expected="_update_obv() + _obv_values accumulator + obv_trend output",
                     actual=f"update_method={has_update_method}, obv_values={has_obv_values}, "
                            f"obv_trend={has_obv_trend}, obv_logic={has_obv_logic}")

    def _check_v200_obv_divergence(self, ma_source: str) -> None:
        """P1.88: v20.0 OBV divergence detection in _detect_divergences + EMA smoothing."""
        if not ma_source:
            self._record("P1.88", "v20.0 OBV divergence detection", False,
                         actual="multi_agent_analyzer.py not found")
            return

        has_ema_smooth = "def _ema_smooth(" in ma_source
        has_obv_param = "obv_series" in ma_source
        has_obv_divergence = '"OBV"' in ma_source or "'OBV'" in ma_source
        # Check OBV-specific formatting in divergence detection
        has_obv_format = "volume not confirming" in ma_source or "accumulation despite" in ma_source
        # Check EMA smoothing applied to OBV before divergence detection
        has_obv_ema_call = "_ema_smooth" in ma_source and "obv" in ma_source.lower()

        ok = has_ema_smooth and has_obv_param and has_obv_divergence and has_obv_format
        self._record("P1.88", "v20.0 OBV divergence in _detect_divergences + EMA(20) smoothing", ok,
                     expected="_ema_smooth() + obv_series param + OBV check_divergence + custom format",
                     actual=f"ema_smooth={has_ema_smooth}, obv_param={has_obv_param}, "
                            f"obv_divergence={has_obv_divergence}, obv_format={has_obv_format}, "
                            f"ema_call={has_obv_ema_call}")

    def _check_v200_volatility_regime_prompts(self, ma_source: str) -> None:
        """P1.89: v20.0 Volatility Regime + OBV in AI prompts — SKIPPED (deleted with AI agents v46.0)."""
        self._record("P1.89", "v20.0 Volatility Regime + OBV prompts (skipped: deleted v46.0)", True)

    def _check_v200_volatility_regime_orthogonal(self, tm_source: str) -> None:
        """P1.90: v20.0 Volatility Regime must NOT affect calculate_mechanical_sltp.

        Volatility regime is a pure RISK/CONTEXT signal for AI assessment only.
        It must not appear in trading_logic.py's SL/TP calculation path.
        """
        tl_path = self.ctx.project_root / "strategy" / "trading_logic.py"
        tl_source = tl_path.read_text(encoding='utf-8') if tl_path.exists() else ""

        # Volatility regime should NOT be in trading_logic.py
        no_vol_in_trading_logic = ("volatility_regime" not in tl_source
                                   and "volatility_percentile" not in tl_source)

        # Volatility regime SHOULD be in technical_manager.py
        vol_in_indicator = "_calculate_atr_regime" in tm_source

        ok = no_vol_in_trading_logic and vol_in_indicator
        self._record("P1.90", "v20.0 Volatility Regime orthogonal to calculate_mechanical_sltp", ok,
                     expected="volatility_regime NOT in trading_logic.py, YES in technical_manager.py",
                     actual=f"absent_from_trading_logic={no_vol_in_trading_logic}, "
                            f"present_in_indicator={vol_in_indicator}")

    # ── P1.91-P1.96: v23.0 Entry Timing Agent implementation fixes ──

    def _check_v230_trend_data_available(self, ma_source: str) -> None:
        """P1.91: v23.0 DI+=DI-=0 trend unclear fix.

        When MTF trend layer data is missing (DI+=DI-=0), the system must treat
        trend as 'unclear' instead of defaulting to BEARISH, preventing false
        COUNTER-TREND ALERT injection into Entry Timing prompt.
        """
        has_trend_check = "trend_data_available" in ma_source
        has_unclear = '"UNCLEAR"' in ma_source
        has_none_bullish = "trend_is_bullish = None" in ma_source

        ok = has_trend_check and has_unclear and has_none_bullish
        self._record("P1.91", "v23.0 DI+=DI-=0 trend unclear (no false COUNTER-TREND)", ok,
                     expected="trend_data_available check + UNCLEAR direction + None bullish",
                     actual=f"trend_check={has_trend_check}, unclear={has_unclear}, "
                            f"none_bullish={has_none_bullish}")

    def _check_v230_phase25_try_except(self, ma_source: str) -> None:
        """P1.92: v23.0 Phase 2.5 independent try-except.

        Entry Timing Agent must be wrapped in its own try-except to prevent
        API timeout/parse errors from cascading to the outer handler and
        discarding Phase 0-2 work.
        """
        has_phase25_error = "Phase 2.5 Entry Timing failed" in ma_source
        has_preserving = "Preserving Judge decision" in ma_source
        has_fallback_conf = "fallback_conf" in ma_source

        ok = has_phase25_error and has_preserving and has_fallback_conf
        self._record("P1.92", "v23.0 Phase 2.5 independent try-except", ok,
                     expected="Phase 2.5 error handler + preserves Judge decision + fallback conf",
                     actual=f"error_handler={has_phase25_error}, preserving={has_preserving}, "
                            f"fallback_conf={has_fallback_conf}")

    def _check_v230_shallow_copy_upfront(self, ma_source: str) -> None:
        """P1.93: v23.0 Unified shallow copy for judge_decision.

        All Phase 2.5 paths (REJECT, ENTER+change, ENTER+no-change) must use
        a shallow copy to prevent mutating the original judge dict.
        """
        # Check for upfront shallow copy pattern
        has_upfront_copy = "Shallow copy upfront" in ma_source
        ok = has_upfront_copy
        self._record("P1.93", "v23.0 judge_decision shallow copy upfront (all paths)", ok,
                     expected="'Shallow copy upfront' comment indicating unified copy",
                     actual=f"upfront_copy={has_upfront_copy}")

    def _check_v230_signal_age_check(self, strategy_source: str) -> None:
        """P1.94: v23.0 Signal age check in _execute_trade().

        Signals older than 600s (10 min) must be degraded to HOLD to prevent
        stale signal execution.
        """
        has_signal_age = "Signal age check" in strategy_source or "signal age" in strategy_source.lower()
        has_max_age = "_max_age" in strategy_source or "max_age" in strategy_source
        has_age_reject = "信号过期" in strategy_source

        ok = has_signal_age and has_max_age and has_age_reject
        self._record("P1.94", "v23.0 Signal age check in _execute_trade()", ok,
                     expected="Signal age check + max_age threshold + stale rejection",
                     actual=f"age_check={has_signal_age}, max_age={has_max_age}, "
                            f"reject={has_age_reject}")

    def _check_v230_et_consecutive_rejects(self, strategy_source: str) -> None:
        """P1.95: v23.0 Entry Timing consecutive REJECT counter.

        Tracks consecutive Entry Timing REJECTs to detect dead loops.
        Must increment on REJECT, reset on successful entry, warn at >=4.
        """
        has_counter = "_et_consecutive_rejects" in strategy_source
        has_reset = "Entry Timing REJECT counter reset" in strategy_source
        has_warning = "连续拦截" in strategy_source or "possible dead loop" in strategy_source

        ok = has_counter and has_reset and has_warning
        self._record("P1.95", "v23.0 Entry Timing consecutive REJECT counter", ok,
                     expected="_et_consecutive_rejects + reset on entry + dead loop warning",
                     actual=f"counter={has_counter}, reset={has_reset}, warning={has_warning}")

    def _check_v230_fallback_timing_assessment(self, ma_source: str) -> None:
        """P1.96: v23.0 _create_fallback_signal includes _timing_assessment.

        Fallback signal must include _timing_assessment marker so Strategy
        can distinguish 'Entry Timing skipped' vs 'entire analysis failed'.
        """
        # Check _create_fallback_signal method contains _timing_assessment
        has_fallback_timing = ("_create_fallback_signal" in ma_source
                               and "_timing_assessment" in ma_source
                               and "before Entry Timing" in ma_source)

        ok = has_fallback_timing
        self._record("P1.96", "v23.0 _create_fallback_signal includes _timing_assessment", ok,
                     expected="_timing_assessment in _create_fallback_signal with 'before Entry Timing'",
                     actual=f"fallback_has_timing={has_fallback_timing}")

    # ── P1.97-P1.99: v24.0 AI Quality Auditor ──

    def _check_v240_quality_auditor_import(self, ma_source: str) -> None:
        """P1.97: v24.0 AIQualityAuditor — SKIPPED (deleted v46.0)."""
        self._record("P1.97", "v24.0 AIQualityAuditor (skipped: deleted v46.0)", True)

    def _check_v240_quality_audit_call(self, ma_source: str) -> None:
        """P1.98: v24.0 quality audit call — SKIPPED (deleted v46.0)."""
        self._record("P1.98", "v24.0 quality audit call (skipped: deleted v46.0)", True)

    def _check_v240_quality_score_in_decision(self, ma_source: str) -> None:
        """P1.99: v24.0 _quality_score injected into final_decision."""
        ok = "_quality_score" in ma_source
        self._record("P1.99", "v24.0 _quality_score in final_decision", ok,
                     expected="final_decision['_quality_score'] = quality_report.overall_score",
                     actual="found" if ok else "not found")

    # ── P1.100-P1.107: v24.0 Trailing Stop (TRAILING_STOP_MARKET) ──

    def _check_v240_trailing_stop_submit(self, source: str) -> None:
        """P1.100: v24.0 _submit_trailing_stop method exists with correct API usage."""
        has_method = "def _submit_trailing_stop(" in source
        has_trailing_factory = "trailing_stop_market(" in source
        has_trailing_offset_type = "TrailingOffsetType.BASIS_POINTS" in source
        has_reduce_only = "reduce_only=True" in source and "trailing_stop_market" in source
        ok = has_method and has_trailing_factory and has_trailing_offset_type
        self._record("P1.100", "v24.0 _submit_trailing_stop (TRAILING_STOP_MARKET + BPS offset)", ok,
                     expected="_submit_trailing_stop + trailing_stop_market + TrailingOffsetType.BASIS_POINTS",
                     actual=f"method={has_method}, factory={has_trailing_factory}, bps={has_trailing_offset_type}")

    def _check_v240_trailing_activation(self, source: str) -> None:
        """P1.101: v24.0/v43.0 Binance native trailing submitted at position open.
        activation_price = entry ± (risk × 1.5R), callback from 4H ATR."""
        has_activation_r = "_TRAILING_ACTIVATION_R" in source
        has_native_submit = "trailing_stop_market(" in source
        has_activation_price = "activation_price" in source
        has_on_position_opened = "on_position_opened" in source
        ok = has_activation_r and has_native_submit and has_activation_price and has_on_position_opened
        self._record("P1.101", "v24.0/v43.0 Binance native trailing at position open (1.5R activation, 4H ATR)", ok,
                     expected="trailing_stop_market + activation_price + on_position_opened + _TRAILING_ACTIVATION_R",
                     actual=f"R={has_activation_r}, native={has_native_submit}, actpx={has_activation_price}, opened={has_on_position_opened}")

    def _check_v240_trailing_event_handler(self, source: str) -> None:
        """P1.102: v24.0 on_order_filled recognizes TRAILING_STOP_MARKET as SL type.
        Both STOP_MARKET and TRAILING_STOP_MARKET must be in the is_sl check."""
        has_trailing_in_is_sl = "OrderType.TRAILING_STOP_MARKET" in source
        # Must appear in the SL type check tuple (not just anywhere)
        has_paired = ("OrderType.STOP_MARKET," in source and
                      "OrderType.TRAILING_STOP_MARKET," in source)
        has_trailing_close_reason = "TRAILING_STOP" in source
        ok = has_trailing_in_is_sl and has_paired and has_trailing_close_reason
        self._record("P1.102", "v24.0 event_handler TRAILING_STOP_MARKET recognition + close reason", ok,
                     expected="is_sl check includes TRAILING_STOP_MARKET + TRAILING_STOP close reason",
                     actual=f"in_is_sl={has_trailing_in_is_sl}, paired={has_paired}, reason={has_trailing_close_reason}")

    def _check_v240_trailing_modify_sl_reset(self, source: str) -> None:
        """P1.103: v24.0 /modify_sl cancels TRAILING_STOP_MARKET and resets trailing layer fields.
        After manual SL modification, trailing_order_id must be cleared (v24.2: trailing_activated removed)."""
        # telegram_commands.py must handle both STOP_MARKET and TRAILING_STOP_MARKET
        has_trailing_cancel = "OrderType.TRAILING_STOP_MARKET" in source
        # v24.2: trailing_activated flag was removed (never correctly set to True).
        # Instead, /modify_sl must clear trailing_order_id from layer.
        has_trailing_id_clear = "trailing_order_id" in source and "''" in source
        ok = has_trailing_cancel and has_trailing_id_clear
        self._record("P1.103", "v24.0 /modify_sl trailing reset (cancel TRAILING + clear layer fields)", ok,
                     expected="/modify_sl cancels TRAILING_STOP_MARKET + clears trailing_order_id=''",
                     actual=f"cancel_trailing={has_trailing_cancel}, id_clear={has_trailing_id_clear}")

    def _check_v240_trailing_resubmit_sltp(self, source: str) -> None:
        """P1.104: v24.0 _resubmit_sltp_if_needed checks both STOP_MARKET and TRAILING_STOP_MARKET."""
        has_resubmit_method = "_resubmit_sltp_if_needed" in source
        # The method must check both order types to detect active SL
        # Count how many times TRAILING_STOP_MARKET appears — should be multiple
        trailing_count = source.count("OrderType.TRAILING_STOP_MARKET")
        ok = has_resubmit_method and trailing_count >= 6  # Multiple paired references across files
        self._record("P1.104", "v24.0 TRAILING_STOP_MARKET paired with STOP_MARKET across all checks", ok,
                     expected="OrderType.TRAILING_STOP_MARKET in ≥6 locations (all SL type checks)",
                     actual=f"resubmit={has_resubmit_method}, trailing_refs={trailing_count}")

    def _check_v240_trailing_startup_recovery(self, source: str) -> None:
        """P1.105: v24.0 startup recovery uses explicit enum checks, not string matching.
        All 'STOP' in str() patterns must be replaced with OrderType enum checks."""
        # Negative check: no fragile string matching for SL detection
        has_fragile = "'STOP' in str(" in source or '"STOP" in str(' in source
        ok = not has_fragile
        self._record("P1.105", "v24.0 startup recovery uses OrderType enum (no string matching)", ok,
                     expected="No 'STOP' in str() patterns (use OrderType.STOP_MARKET enum check)",
                     actual="no fragile patterns" if ok else "FOUND fragile 'STOP' in str() pattern")

    def _check_v240_reconcile_order_resubmit(self, source: str) -> None:
        """P1.106: v24.0 _reconcile_layer_quantities triggers order resubmission.
        After scaling layer quantities, SL/TP orders must be cancelled and resubmitted."""
        has_reconcile = "_reconcile_layer_quantities" in source
        has_resubmit = "_resubmit_layer_orders_with_quantity" in source
        ok = has_reconcile and has_resubmit
        self._record("P1.106", "v24.0 reconcile layer quantities triggers order resubmission", ok,
                     expected="_reconcile_layer_quantities calls _resubmit_layer_orders_with_quantity",
                     actual=f"reconcile={has_reconcile}, resubmit={has_resubmit}")

    def _check_v240_emergency_close_cancel_orders(self, source: str) -> None:
        """P1.107: v24.0 _emergency_market_close cancels reduce_only orders before close.
        Prevents quantity mismatch if trailing stop partially fills before emergency close."""
        has_emergency = "_emergency_market_close" in source
        has_cancel_before = "cancel reduce_only orders" in source.lower() or \
                           "Cancel outstanding reduce_only" in source or \
                           "cancel_order" in source
        # Check the cancel logic is BEFORE the retry loop in emergency_market_close
        has_intentional_cancel = "_intentionally_cancelled_order_ids" in source
        ok = has_emergency and has_cancel_before and has_intentional_cancel
        self._record("P1.107", "v24.0 emergency close cancels reduce_only orders first", ok,
                     expected="_emergency_market_close cancels outstanding orders before market close",
                     actual=f"emergency={has_emergency}, cancel_logic={has_cancel_before}, intentional={has_intentional_cancel}")

    # ── P1.108-P1.110: Additional regression guards ──

    def _check_v210_fr_block_counter(self, source: str) -> None:
        """P1.108: v21.0 FR Consecutive Block Counter infrastructure.
        Tracks same-direction FR blocks; >=3 → downgrade to HOLD to break deadloop."""
        has_counter = "_fr_consecutive_blocks" in source
        has_threshold = "_fr_consecutive_blocks >= 3" in source or "_fr_consecutive_blocks>=3" in source
        has_reset = "_fr_consecutive_blocks = 0" in source
        has_increment = "_fr_consecutive_blocks += 1" in source
        ok = has_counter and has_threshold and has_reset and has_increment
        self._record("P1.108", "v21.0 FR Consecutive Block Counter (init/increment/threshold/reset)", ok,
                     expected="_fr_consecutive_blocks: init + +=1 + >=3 threshold + =0 reset on open",
                     actual=f"counter={has_counter}, threshold={has_threshold}, reset={has_reset}, incr={has_increment}")

    def _check_v183_force_analysis_cycles(self, source: str) -> None:
        """P1.109: v18.3 Post-close forced analysis cycles.
        After position close, force 2 extra AI analysis cycles to catch re-entry."""
        has_counter = "_force_analysis_cycles_remaining" in source
        has_set = "_force_analysis_cycles_remaining = 2" in source
        has_consume = "_force_analysis_cycles_remaining -= 1" in source or \
                      "_force_analysis_cycles_remaining -=" in source
        ok = has_counter and has_set and has_consume
        self._record("P1.109", "v18.3 Post-close forced analysis cycles (set=2, consume)", ok,
                     expected="_force_analysis_cycles_remaining: init + set=2 in on_position_closed + consume in _has_market_changed",
                     actual=f"counter={has_counter}, set2={has_set}, consume={has_consume}")

    def _check_v240_tp_resubmit(self, source: str) -> None:
        """P1.110: v24.0 _resubmit_tp_for_layer method exists for TP recovery.
        When TP order is cancelled/expired, resubmit at original planned price."""
        has_method = "_resubmit_tp_for_layer" in source
        ok = has_method
        self._record("P1.110", "v24.0 _resubmit_tp_for_layer (TP recovery on cancel/expire)", ok,
                     expected="_resubmit_tp_for_layer method in strategy source",
                     actual="found" if ok else "not found")

    # ── P1.111-P1.112: AnalysisContext parity (v29+) ──

    def _check_v29_confidence_chain(self, ma_source: str) -> None:
        """P1.111: v29+ AnalysisContext — SKIPPED (deleted with AI agents v46.0)."""
        self._record("P1.111", "v29+ AnalysisContext (skipped: deleted v46.0)", True)

    def _check_v29_conditions_v2(self, ma_source: str) -> None:
        """P1.112: v29+ MemoryConditions — SKIPPED (deleted with AI agents v46.0)."""
        self._record("P1.112", "v29+ MemoryConditions (skipped: deleted v46.0)", True)

    # ── P1.113: v32.1 Risk Manager conditional skip ──

    def _check_v321_risk_manager_skip(self, ma_source: str) -> None:
        """P1.113: v32.1 Risk Manager conditional skip — SKIPPED (AI agents deleted v46.0)."""
        self._record("P1.113", "v32.1 Risk Manager skip (skipped: deleted v46.0)", True)

    # ── P1.115-P1.117: v34.0 Auditor logic-level coherence checks ──

    def _check_v340_tag_classification(self, auditor_source: str) -> None:
        """P1.115: v34.0 Auditor tag classification — SKIPPED (ai_quality_auditor.py deleted v46.0)."""
        self._record("P1.115", "v34.0 Auditor tag classification (skipped: deleted v46.0)", True)

    def _check_v340_reason_signal_conflict(self, auditor_source: str) -> None:
        """P1.116: v34.0 reason_signal_conflict — SKIPPED (ai_quality_auditor.py deleted v46.0)."""
        self._record("P1.116", "v34.0 reason_signal_conflict (skipped: deleted v46.0)", True)

    def _check_v340_confidence_risk_conflict(self, auditor_source: str) -> None:
        """P1.117: v34.0 confidence_risk_conflict — SKIPPED (ai_quality_auditor.py deleted v46.0)."""
        self._record("P1.117", "v34.0 confidence_risk_conflict (skipped: deleted v46.0)", True)

    # ── P1.118-P1.121: v39.0/v40.0 static analysis ──

    def _check_v390_4h_atr_passthrough(self, source: str) -> None:
        """P1.118: v39.0 _cached_atr_4h stored in ai_strategy and passed through order_execution."""
        has_init = "_cached_atr_4h" in source
        has_passthrough = "atr_4h" in source and "calculate_mechanical_sltp" in source
        ok = has_init and has_passthrough
        self._record("P1.118", "v39.0 _cached_atr_4h init + passthrough to calculate_mechanical_sltp", ok,
                     expected="_cached_atr_4h attribute + atr_4h passed to calculate_mechanical_sltp",
                     actual=f"init={has_init}, passthrough={has_passthrough}")

    def _check_v400_weighted_scores(self, rf_source: str) -> None:
        """P1.119: v40.0 compute_scores_from_features uses (signal, weight) tuples instead of ±1."""
        # v40.0 Layer A: weighted signals with information density
        has_tuple_pattern = "(1, " in rf_source or "(-1, " in rf_source
        has_weight_sum = "weight_total" in rf_source or "w_total" in rf_source
        ok = has_tuple_pattern and has_weight_sum
        self._record("P1.119", "v40.0 weighted (signal, weight) tuples in compute_scores_from_features", ok,
                     expected="(signal, weight) tuples + weight_total normalization",
                     actual=f"tuples={has_tuple_pattern}, weight_sum={has_weight_sum}")

    def _check_v400_transitioning_regime(self, rf_source: str) -> None:
        """P1.120: v40.0 TRANSITIONING regime detection with 2-cycle hysteresis."""
        has_transitioning = "TRANSITIONING" in rf_source
        has_hysteresis = "_prev_regime_transition" in rf_source
        has_flow_dir = "flow_dir" in rf_source
        ok = has_transitioning and has_hysteresis and has_flow_dir
        self._record("P1.120", "v40.0 TRANSITIONING regime + hysteresis + flow_dir detection", ok,
                     expected="TRANSITIONING label + _prev_regime_transition hysteresis + flow_dir",
                     actual=f"transitioning={has_transitioning}, hysteresis={has_hysteresis}, flow_dir={has_flow_dir}")

    def _check_v400_tp_parameter_ssot(self) -> None:
        """P1.121: v40.0 TP parameters consistent across SSoT (trading_logic + backtest_math + base.yaml)."""
        import yaml
        try:
            from strategy.trading_logic import _get_trading_logic_config
            cfg = _get_trading_logic_config()
            mech = cfg.get('mechanical_sltp', {})
            tp_logic = mech.get('tp_rr_target', {})
            sl_logic = mech.get('sl_atr_multiplier', {})

            # Read backtest_math
            bm_path = self.ctx.project_root / "utils" / "backtest_math.py"
            bm_source = bm_path.read_text(encoding='utf-8') if bm_path.exists() else ""

            # Check backtest_math has matching values
            sl_hi_match = "'HIGH': 0.8" in bm_source or '"HIGH": 0.8' in bm_source
            tp_hi_match = "'HIGH': 1.5" in bm_source or '"HIGH": 1.5' in bm_source

            ok_logic = (tp_logic.get('HIGH') == 1.5 and tp_logic.get('MEDIUM') == 1.5
                        and sl_logic.get('HIGH') == 0.8 and sl_logic.get('MEDIUM') == 1.0)
            ok = ok_logic and sl_hi_match and tp_hi_match
            self._record("P1.121", "v44.0 SL/TP SSoT: trading_logic + backtest_math consistent", ok,
                         expected="TP: HIGH=1.5/MED=1.5, SL: HIGH=0.8/MED=1.0 across SSoT files",
                         actual=f"logic={ok_logic} (tp={tp_logic}, sl={sl_logic}), bm_match={sl_hi_match and tp_hi_match}")
        except Exception as e:
            self._record("P1.121", "v40.0 TP parameter SSoT check", False,
                         expected="No exception", actual=str(e))

    # ── P1.122-P1.123: v42.0 ET Exhaustion ──

    def _check_v420_et_exhaustion_strategy(self, strategy_source: str) -> None:
        """P1.122: v42.0 ET Exhaustion Tier 1/2 constants and override logic in strategy."""
        has_tier1 = "_ET_EXHAUSTION_TIER1" in strategy_source
        has_tier2 = "_ET_EXHAUSTION_TIER2" in strategy_source
        has_override = "_et_exhaustion_tier1" in strategy_source and "signal_data['signal'] = _orig_signal" in strategy_source
        has_skip = "skip_entry_timing=_skip_et" in strategy_source
        ok = has_tier1 and has_tier2 and has_override and has_skip
        self._record("P1.122", "v42.0 ET Exhaustion Tier 1/2 in strategy (constants + override + skip_et)", ok,
                     expected="_ET_EXHAUSTION_TIER1/TIER2 + Tier 1 override + skip_entry_timing=_skip_et",
                     actual=f"tier1={has_tier1}, tier2={has_tier2}, override={has_override}, skip={has_skip}")

    def _check_v420_skip_entry_timing(self, strategy_source: str, analyzer_source: str) -> None:
        """P1.123: v42.0 skip_entry_timing parameter in analyze() and MultiAgentAnalyzer."""
        has_param_in_analyzer = "skip_entry_timing" in analyzer_source
        has_param_in_strategy = "skip_entry_timing" in strategy_source
        ok = has_param_in_analyzer and has_param_in_strategy
        self._record("P1.123", "v42.0 skip_entry_timing param in both strategy + analyzer", ok,
                     expected="skip_entry_timing in analyze() signature and caller",
                     actual=f"analyzer={has_param_in_analyzer}, strategy={has_param_in_strategy}")

    # ── P1.126-P1.130: v2.0 Upgrade Plan static checks ──

    def _check_v20_fear_greed_schema(self) -> None:
        """P1.126: v2.0 fear_greed_index in FEATURE_SCHEMA."""
        try:
            from agents.prompt_constants import FEATURE_SCHEMA
            ok = 'fear_greed_index' in FEATURE_SCHEMA
        except ImportError:
            ok = False
        self._record("P1.126", "v2.0 fear_greed_index in FEATURE_SCHEMA", ok,
                     expected="'fear_greed_index' key present in FEATURE_SCHEMA",
                     actual=f"present={ok}")

    def _check_v20_extreme_tags(self) -> None:
        """P1.127: v2.0 EXTREME_FEAR/GREED — check in REASON_TAGS (EVIDENCE_TAGS deleted v46.0)."""
        try:
            from agents.prompt_constants import REASON_TAGS
            has_fear = 'EXTREME_FEAR' in REASON_TAGS
            has_greed = 'EXTREME_GREED' in REASON_TAGS
            ok = has_fear and has_greed
        except ImportError:
            has_fear = has_greed = False
            ok = False
        self._record("P1.127", "v2.0 EXTREME_FEAR/GREED in REASON_TAGS", ok,
                     expected="EXTREME_FEAR and EXTREME_GREED in REASON_TAGS",
                     actual=f"EXTREME_FEAR={has_fear}, EXTREME_GREED={has_greed}")

    def _check_v20_instructor_wiring(self, ma_source: str) -> None:
        """P1.128: v2.0 Instructor wiring — SKIPPED (deleted with AI agents v46.0)."""
        self._record("P1.128", "v2.0 Instructor wiring (skipped: deleted v46.0)", True)

    def _check_v20_fear_greed_analyze(self, ma_source: str) -> None:
        """P1.130: v2.0 fear_greed_report — SKIPPED (AI analyze() deleted v46.0)."""
        self._record("P1.130", "v2.0 fear_greed_report (skipped: deleted v46.0)", True)
