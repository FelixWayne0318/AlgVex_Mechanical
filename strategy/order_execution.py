"""
Order Execution Mixin

Extracted from ai_strategy.py for code organization.
Contains trade execution pipeline: _execute_trade, _calculate_position_size,
_manage_existing_position, _validate_sltp_for_entry, _open_new_position,
_adjust_tp_for_fill_price, _validate_and_adjust_rr_post_fill.
"""

import math
import time
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timezone

from decimal import Decimal

from nautilus_trader.model.enums import OrderSide, OrderType, TriggerType, TrailingOffsetType
from nautilus_trader.model.position import Position

from strategy.trading_logic import (
    calculate_position_size,
    calculate_mechanical_sltp,
    calculate_dca_sltp,
    get_default_sl_pct,
    get_default_tp_pct_buy,
    get_min_rr_ratio,
    get_counter_trend_rr_multiplier,
    _is_counter_trend,
)


class OrderExecutionMixin:
    """Mixin providing order execution methods for AITradingStrategy."""

    def _execute_trade(
        self,
        signal_data: Dict[str, Any],
        price_data: Dict[str, Any],
        technical_data: Dict[str, Any],
        current_position: Optional[Dict[str, Any]],
    ):
        """
        Execute trading logic based on signal.

        Parameters
        ----------
        signal_data : Dict
            AI-generated signal
        price_data : Dict
            Current price data
        technical_data : Dict
            Technical indicators
        current_position : Dict or None
            Current position info
        """
        # Check if trading is paused (thread-safe read)
        with self._state_lock:
            if self.is_trading_paused:
                self.log.info("⏸️ Trading is paused - skipping signal execution")
                # v4.1: Update signal status
                self._last_signal_status = {
                    'executed': False,
                    'reason': '交易已暂停',
                    'action_taken': '',
                }
                return

        # Store signal and technical data for SL/TP calculation
        self.latest_signal_data = signal_data
        self.latest_technical_data = technical_data
        self.latest_price_data = price_data

        signal = signal_data['signal']
        confidence = signal_data['confidence']

        # v23.0: System-level signal age check — reject stale signals before execution.
        # This guards against edge cases where analysis takes too long (API retries,
        # network delays) and the signal timestamp is far behind current time.
        if signal in ('LONG', 'SHORT'):
            _signal_ts_str = signal_data.get('timestamp', '')
            if _signal_ts_str:
                try:
                    # Handle both formats: ISO 8601 and strftime
                    if 'T' in _signal_ts_str:
                        _signal_ts = datetime.fromisoformat(_signal_ts_str.replace('Z', '+00:00'))
                    else:
                        _signal_ts = datetime.strptime(_signal_ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    _age_secs = (datetime.now(timezone.utc) - _signal_ts).total_seconds()
                    _max_age = 600  # 10 minutes — half of 20-min cycle
                    if _age_secs > _max_age:
                        self.log.warning(
                            f"⚠️ Signal age check: {signal} signal is {_age_secs:.0f}s old "
                            f"(max {_max_age}s). Degrading to HOLD."
                        )
                        self._last_signal_status = {
                            'executed': False,
                            'reason': f'信号过期 ({_age_secs:.0f}s > {_max_age}s)',
                            'action_taken': '',
                        }
                        return
                except (ValueError, TypeError) as e:
                    self.log.debug(f"Signal age check: could not parse timestamp '{_signal_ts_str}': {e}")

        # v3.12: Normalize legacy signals (BUY→LONG, SELL→SHORT)
        legacy_mapping = {'BUY': 'LONG', 'SELL': 'SHORT'}
        if signal in legacy_mapping:
            signal = legacy_mapping[signal]
            signal_data['signal'] = signal  # Update for downstream use

        # v46.0: FR exhaustion gate removed — mechanical mode factors FR into
        # anticipatory scoring (Order Flow dimension). No separate gate needed.

        # v15.0 P2: Track confidence decay for open positions
        if current_position:
            self._check_confidence_decay(signal_data)

        # Check minimum confidence (skip for CLOSE and REDUCE - always allow risk reduction)
        if signal not in ('CLOSE', 'REDUCE', 'HOLD'):
            confidence_levels = {'LOW': 0, 'MEDIUM': 1, 'HIGH': 2}
            min_conf_level = confidence_levels.get(self.min_confidence, 1)
            signal_conf_level = confidence_levels.get(confidence, 1)

            if signal_conf_level < min_conf_level:
                self.log.warning(
                    f"⚠️ Signal confidence {confidence} below minimum {self.min_confidence}, skipping trade"
                )
                self._last_signal_status = {
                    'executed': False,
                    'reason': f'信心不足 ({confidence} < {self.min_confidence})',
                    'action_taken': '',
                }
                return

        # Handle HOLD signal
        if signal == 'HOLD':
            # v11.0-simple: HOLD with existing position → let trade play out
            # SL/TP + Time Barrier manage the exit
            if current_position:
                self.log.info(
                    "📊 Signal: HOLD with existing position — "
                    "letting trade play out (SL/TP + Time Barrier active)"
                )
            else:
                self.log.info("📊 Signal: HOLD - No action taken")
            # v34.2: Propagate hold_source from AI decision (explicit_judge)
            _hold_src = signal_data.get('hold_source', 'explicit_judge')
            self._last_signal_status = {
                'executed': False,
                'reason': 'AI 建议观望',
                'action_taken': '',
                'hold_source': _hold_src,
            }
            return

        # v3.12: Handle CLOSE signal - close position without opening opposite
        if signal == 'CLOSE':
            if not current_position:
                self.log.info("📊 Signal: CLOSE - No position to close")
                self._last_signal_status = {
                    'executed': False,
                    'reason': '无持仓可平',
                    'action_taken': '',
                }
                return

            # Close the existing position
            self._close_position_only(current_position)
            return

        # v3.12: Handle REDUCE signal - reduce position size but keep direction
        if signal == 'REDUCE':
            if not current_position:
                self.log.info("📊 Signal: REDUCE - No position to reduce")
                self._last_signal_status = {
                    'executed': False,
                    'reason': '无持仓可减',
                    'action_taken': '',
                }
                return

            # Calculate target size from position_size_pct
            position_size_pct = signal_data.get('position_size_pct', 50)
            self._reduce_position(current_position, position_size_pct)
            return

        # For LONG/SHORT signals, calculate position size
        calculated_quantity = self._calculate_position_size(
            signal_data, price_data, technical_data, current_position
        )

        # v4.8: In cumulative mode, calculated_quantity is "this layer add-on amount"
        # Need to convert to "target position" for _manage_existing_position
        if self.position_sizing_cumulative and current_position:
            current_qty = current_position.get('quantity', 0)
            target_quantity = current_qty + calculated_quantity
            self.log.info(
                f"📊 累加模式: 当前 {current_qty:.4f} + 加仓 {calculated_quantity:.4f} = 目标 {target_quantity:.4f} BTC"
            )
        else:
            target_quantity = calculated_quantity

        if target_quantity == 0 and calculated_quantity == 0:
            self.log.warning("⚠️ Calculated position size is 0, skipping trade")
            self._last_signal_status = {
                'executed': False,
                'reason': '仓位计算为0 (余额不足)',
                'action_taken': '',
            }

            # Notify user about insufficient position size
            if self.telegram_bot and self.enable_telegram and self.telegram_notify_errors:
                try:
                    current_price = price_data.get('price', 0) if price_data else 0
                    _sig_cn = self.telegram_bot.side_to_cn(signal, 'side') if signal in ('LONG', 'SHORT') else signal
                    error_msg = self.telegram_bot.format_error_alert({
                        'type': 'POSITION_SIZE_ZERO',
                        'message': f"无法执行 {_sig_cn} 信号 - 仓位计算为 0",
                        'details': f"价格: ${current_price:.2f}, 信号: {_sig_cn} ({confidence})",
                        'action': "检查账户余额或调整仓位参数"
                    })
                    self.telegram_bot.send_message_sync(error_msg)
                except Exception as notify_error:
                    self.log.error(f"Failed to send Telegram alert: {notify_error}")

            return

        # v3.12: Determine order side from normalized signal
        target_side = 'long' if signal == 'LONG' else 'short'

        # v4.0: Store execution data for unified Telegram notification in on_position_opened
        # This replaces the separate signal/fill/position notifications with one comprehensive message
        if self.telegram_bot and self.enable_telegram:
            judge_info = signal_data.get('judge_decision', {})
            _ta = signal_data.get('_timing_assessment', {})
            self._pending_execution_data = {
                'signal': signal,
                'confidence': confidence,
                'target_quantity': target_quantity,
                'price': price_data.get('price', 0),
                'rsi': technical_data.get('rsi'),
                'macd': technical_data.get('macd'),
                'winning_side': judge_info.get('winning_side', ''),
                'reasoning': signal_data.get('reason', ''),
                'judge_rationale': judge_info.get('rationale', ''),
                'risk_level': signal_data.get('risk_level', 'MEDIUM'),
                'position_size_pct': signal_data.get('position_size_pct'),
                # v5.7: Pass confluence for Telegram display
                'confluence': judge_info.get('confluence', {}),
                # v23.0: Entry Timing assessment for Telegram display
                'timing_verdict': _ta.get('timing_verdict', ''),
                'timing_quality': _ta.get('timing_quality', ''),
                # v47.0: Anticipatory scores for Telegram display
                'anticipatory_scores': signal_data.get('_anticipatory_scores'),
            }
            # v19.2: Add pre-computed flow signals for trade execution display
            try:
                flow_signals = self._compute_flow_signals()
                if flow_signals:
                    self._pending_execution_data['flow_signals'] = flow_signals
            except Exception as e:
                self.log.debug(f"Flow signals for execution: {e}")
            # v29.0: Add S/R calibration summary for trade execution display
            try:
                from utils.calibration_loader import get_calibration_summary
                self._pending_execution_data['calibration'] = get_calibration_summary()
            except Exception as e:
                self.log.debug(f"Calibration summary for execution: {e}")

        # v6.6: Re-verify position state before execution.
        # AI analysis takes 15-45 seconds; SL/TP may have triggered during that window.
        fresh_position = self._get_current_position_data()
        if current_position and not fresh_position:
            self.log.warning(
                "⚠️ Position closed during AI analysis (SL/TP triggered). "
                "Skipping stale signal execution."
            )
            self._last_signal_status = {
                'executed': False,
                'reason': 'AI 分析期间仓位已平 (SL/TP 触发)',
                'action_taken': '',
            }
            return
        # Use fresh position data for execution
        current_position = fresh_position

        # v17.1: Liquidation buffer hard floor — block add-on when buffer critically low
        # AI prompt (STEP 3) handles 10-15% range; code catches extreme cases AI might miss
        if current_position and signal in ('LONG', 'SHORT'):
            liq_buffer = current_position.get('liquidation_buffer_pct')
            if liq_buffer is not None and liq_buffer < 5:
                self.log.warning(
                    f"🚨 Liquidation buffer {liq_buffer:.1f}% < 5% — "
                    f"blocking {signal} to protect existing position"
                )
                self._last_signal_status = {
                    'executed': False,
                    'reason': f'清算缓冲过低 ({liq_buffer:.1f}% < 5%)',
                    'action_taken': '',
                }
                if self.telegram_bot and self.enable_telegram and self.telegram_notify_errors:
                    try:
                        _sig_cn = self.telegram_bot.side_to_cn(signal, 'side') if signal in ('LONG', 'SHORT') else signal
                        error_msg = self.telegram_bot.format_error_alert({
                            'level': 'CRITICAL',
                            'message': f"阻止 {_sig_cn} — 清算缓冲 {liq_buffer:.1f}% < 5%",
                            'context': "仓位保持不变，建议减仓或平仓。查看 /risk 或 /position 了解详情",
                        })
                        self.telegram_bot.send_message_sync(error_msg)
                    except Exception as notify_error:
                        self.log.error(f"Failed to send Telegram alert: {notify_error}")
                return

        # Execute position management logic
        if current_position:
            # v11.0-simple: No partial close on confidence degradation
            # SL/TP + Time Barrier handle all exits
            self._manage_existing_position(
                current_position, target_side, target_quantity, confidence
            )

            # v6.0: Update confidence tracking after managing position
            self._last_position_confidence = confidence
        else:
            # v6.0: Store entry confidence for new position
            self._position_entry_confidence = confidence
            self._last_position_confidence = confidence
            # v15.0 P2: Initialize confidence decay tracking for new position
            self._confidence_history = [confidence]
            self._decay_warned_levels = set()
            self._open_new_position(target_side, target_quantity)

        # v3.11: Add action_taken to pending execution data for Telegram notification
        # This allows Telegram to show specific action (open long/close short/reverse etc.) instead of just BUY/SELL
        if self._pending_execution_data and self._last_signal_status:
            self._pending_execution_data['action_taken'] = self._last_signal_status.get('action_taken', '')
            self._pending_execution_data['was_executed'] = self._last_signal_status.get('executed', False)

        # Note: Telegram notification is now sent in on_position_opened for new positions
        # This ensures we have accurate fill price and SL/TP info

    def _calculate_position_size(
        self,
        signal_data: Dict[str, Any],
        price_data: Dict[str, Any],
        technical_data: Dict[str, Any],
        current_position: Optional[Dict[str, Any]],
    ) -> float:
        """
        Calculate intelligent position size.

        Uses shared trading_logic module to ensure consistency with diagnostic tool.
        Returns BTC quantity based on confidence, trend, and RSI.
        """
        # Create a simple logger adapter that uses self.log
        class LogAdapter:
            def __init__(self, strategy_log):
                self._log = strategy_log
            def info(self, msg):
                self._log.info(msg)
            def warning(self, msg):
                self._log.warning(msg)
            def error(self, msg):
                self._log.error(msg)

        logger = LogAdapter(self.log)

        # v48.0: DCA base_order_pct sizing — simple equity × % / price
        # v49.0: Enforce Binance minimum notional ($100)
        _dca_cfg = getattr(self, '_dca_config', {})
        if _dca_cfg.get('enabled') and self._strategy_mode == 'mechanical':
            base_pct = _dca_cfg.get('base_order_pct', 10.0)
            price = price_data.get('price', 0)
            if price > 0 and self.equity > 0:
                base_usdt = self.equity * (base_pct / 100)
                # Use Binance exchange filter (dynamic, fetched at startup)
                min_notional = float(self.instrument.min_notional) * 1.01 if self.instrument.min_notional else 101.0
                if base_usdt < min_notional:
                    self.log.info(
                        f"📊 DCA sizing: {base_pct}% of ${self.equity:,.0f} = "
                        f"${base_usdt:,.0f} < min ${min_notional:.0f}, using min"
                    )
                    base_usdt = min_notional
                qty = base_usdt / price
                self.log.info(
                    f"📊 DCA sizing: {base_pct}% of ${self.equity:,.0f} = "
                    f"${base_usdt:,.0f} = {qty:.6f} BTC @ ${price:,.0f}"
                )
                return qty
            return 0.0

        # v15.1: Guard against stale config-default equity after restart
        # If use_real_balance_as_equity is enabled but Binance balance hasn't been fetched yet,
        # self.equity still holds config default (1000), which can overshoot real max_usdt
        if self.config.use_real_balance_as_equity and not self._equity_synced:
            self.log.warning(
                f"⚠️ Equity not yet synced from Binance (still config default ${self.equity:.2f}), "
                f"skipping position sizing until real balance is confirmed"
            )
            return 0.0

        # Build config dict from instance variables
        config = {
            'base_usdt': self.base_usdt,
            'equity': self.equity,
            'leverage': self.leverage,  # v4.8: Add leverage
            'high_confidence_multiplier': self.position_config.get('high_confidence_multiplier', 1.5),
            'medium_confidence_multiplier': self.position_config.get('medium_confidence_multiplier', 1.0),
            'low_confidence_multiplier': self.position_config.get('low_confidence_multiplier', 0.5),
            'trend_strength_multiplier': self.position_config.get('trend_strength_multiplier', 1.2),
            'rsi_extreme_multiplier': self.rsi_extreme_mult,
            'rsi_extreme_upper': self.rsi_extreme_upper,
            'rsi_extreme_lower': self.rsi_extreme_lower,
            'max_position_ratio': self.position_config.get('max_position_ratio', 0.3),
            'min_trade_amount': self.position_config.get('min_trade_amount', 0.001),
            'position_sizing': self.position_sizing_config,  # v4.8: Add position sizing config
        }

        # v2.0: Kelly position sizing override (if enabled)
        _kelly = getattr(self, '_kelly_sizer', None)
        if _kelly and getattr(_kelly, '_enabled', False):
            try:
                _regime = getattr(self, '_current_regime', 'RANGING')
                _dd_pct = getattr(self.risk_controller, 'metrics', None)
                _dd = (_dd_pct.drawdown_pct * 100) if _dd_pct else 0.0
                _dd_thresh = getattr(self.risk_controller, 'dd_halt_threshold', 0.15) * 100
                kelly_pct, kelly_details = _kelly.calculate(
                    confidence=signal_data.get('confidence', 'MEDIUM'),
                    regime=_regime,
                    current_dd_pct=_dd,
                    dd_threshold_pct=_dd_thresh,
                )
                if kelly_pct > 0:
                    signal_data['position_size_pct'] = kelly_pct
                    self.log.info(
                        f"[Kelly] size={kelly_pct:.1f}% "
                        f"(source={kelly_details.get('source', 'unknown')}, regime={_regime})"
                    )
            except Exception as e:
                self.log.debug(f"[Kelly] Fallback to default sizing: {e}")

        # v39.0: Pass 4H ATR for risk clamp consistency with SL/TP calculation
        _atr_4h = getattr(self, '_cached_atr_4h', 0.0) or 0.0
        btc_quantity, details = calculate_position_size(
            signal_data, price_data, technical_data, config, logger,
            atr_4h=_atr_4h,
        )

        # v3.12: Apply risk controller position size multiplier (REDUCED = 0.5x)
        risk_mult = signal_data.get('_risk_position_multiplier', 1.0)
        if risk_mult < 1.0 and btc_quantity > 0:
            original_qty = btc_quantity
            _step = float(self.instrument.size_increment)
            btc_quantity = math.floor(btc_quantity * risk_mult / _step) * _step
            self.log.info(
                f"⚠️ Risk multiplier applied: {original_qty:.4f} × {risk_mult:.1f} = {btc_quantity:.4f} BTC"
            )

        # v4.8: Cumulative mode - calculates "this add-on amount" not "target position"
        if self.position_sizing_cumulative and current_position:
            # In cumulative mode, btc_quantity is the amount to add this time
            # Need to check if it exceeds max_usdt limit
            current_qty = current_position.get('quantity', 0)
            current_price = price_data.get('price', 100000)
            current_value = current_qty * current_price

            max_usdt = details.get('max_usdt', self.equity * self.leverage * self.position_config.get('max_position_ratio', 0.3))
            remaining_capacity = max_usdt - current_value

            if remaining_capacity <= 0:
                self.log.warning(
                    f"⚠️ 仓位已达上限 (${current_value:.0f} >= ${max_usdt:.0f}), 无法加仓"
                )
                return 0.0

            # Limit add-on amount to remaining capacity
            max_add_btc = remaining_capacity / current_price
            if btc_quantity > max_add_btc:
                self.log.info(
                    f"📊 加仓受限: {btc_quantity:.4f} → {max_add_btc:.4f} BTC "
                    f"(剩余容量: ${remaining_capacity:.0f})"
                )
                btc_quantity = max_add_btc

        return btc_quantity

    def _manage_existing_position(
        self,
        current_position: Dict[str, Any],
        target_side: str,
        target_quantity: float,
        confidence: str,
    ):
        """Manage existing position (add, reduce, or reverse)."""
        current_side = current_position['side']
        current_qty = current_position['quantity']

        # Same direction - adjust position
        if target_side == current_side:
            size_diff = target_quantity - current_qty
            threshold = self.position_config['adjustment_threshold']

            if abs(size_diff) < threshold:
                self.log.info(
                    f"✅ Position size appropriate ({current_qty:.3f} BTC), no adjustment needed"
                )

                # v7.2: Confidence degradation SL tightening removed.
                # Per-layer SL/TP are independent.

                # v4.1: Update signal status - same direction, holding
                side_cn = '多' if current_side == 'long' else '空'
                self._last_signal_status = {
                    'executed': False,
                    'reason': f'已持有{side_cn}仓 ({current_qty:.4f} BTC)',
                    'action_taken': '维持现有仓位',
                }
                return

            if size_diff > 0:
                # v48.0: DCA mode gate — when DCA is enabled, enforce DCA rules
                # instead of pyramiding rules. DCA requires price to drop spacing_pct
                # from last entry before adding a layer.
                _dca_cfg = getattr(self, '_dca_config', {})
                _is_dca = _dca_cfg.get('enabled') and getattr(self, '_strategy_mode', '') == 'mechanical'
                if _is_dca:
                    dca_max = _dca_cfg.get('max_real_layers', 4)
                    dca_spacing = _dca_cfg.get('spacing_pct', 0.03)
                    n_layers = len(getattr(self, '_layer_orders', {}))

                    # DCA Rule 1: Max layers
                    if n_layers >= dca_max:
                        side_cn = '多' if target_side == 'long' else '空'
                        self.log.info(
                            f"🚫 DCA 加仓被拒: 已达最大层数 {n_layers}/{dca_max} | "
                            f"维持{side_cn}仓 ({current_qty:.4f} BTC)"
                        )
                        self._last_signal_status = {
                            'executed': False,
                            'reason': f'DCA 最大层数 {n_layers}/{dca_max}',
                            'action_taken': '维持现有仓位',
                        }
                        return

                    # DCA Rule 2: Spacing — price must drop spacing_pct from last entry
                    layers_sorted = sorted(
                        getattr(self, '_layer_orders', {}).values(),
                        key=lambda x: x.get('layer_index', 0),
                    )
                    if layers_sorted:
                        last_entry = layers_sorted[-1].get('entry_price', 0)
                        current_price = (self.latest_price_data or {}).get('price', 0) or 0
                        if last_entry > 0 and current_price > 0:
                            pos_side = current_position.get('side', 'long').lower()
                            if pos_side == 'long':
                                drop = (last_entry - current_price) / last_entry
                            else:
                                drop = (current_price - last_entry) / last_entry
                            if drop < dca_spacing:
                                side_cn = '多' if target_side == 'long' else '空'
                                self.log.info(
                                    f"🚫 DCA 加仓被拒: 价差不足 {drop:.2%} < {dca_spacing:.0%} | "
                                    f"上次入场 ${last_entry:,.0f} 当前 ${current_price:,.0f} | "
                                    f"维持{side_cn}仓 ({current_qty:.4f} BTC)"
                                )
                                self._last_signal_status = {
                                    'executed': False,
                                    'reason': f'DCA 价差不足 ({drop:.2%} < {dca_spacing:.0%})',
                                    'action_taken': '维持现有仓位',
                                }
                                return

                    # DCA passed — fall through to pyramiding gate for other checks
                    self.log.info(
                        f"✅ DCA gate passed: layer {n_layers+1}/{dca_max}, spacing OK"
                    )

                # v6.0: Pyramiding gate — enforce all pyramiding rules before adding
                pyramid_allowed, pyramid_reason, layer_ratio = self._check_pyramiding_allowed(
                    current_position=current_position,
                    confidence=confidence,
                    signal_data=self.latest_signal_data or {},
                )
                if not pyramid_allowed:
                    side_cn = '多' if target_side == 'long' else '空'
                    self.log.info(
                        f"🚫 加仓被拒 (金字塔规则): {pyramid_reason} | "
                        f"维持{side_cn}仓 ({current_qty:.4f} BTC)"
                    )
                    self._last_signal_status = {
                        'executed': False,
                        'reason': f'加仓被拒: {pyramid_reason}',
                        'action_taken': '维持现有仓位',
                    }
                    return

                # v6.0: Apply layer-based sizing (override calculated quantity)
                # Layer ratio determines what fraction of max_position this add should be
                current_price = (self.latest_price_data or {}).get('price', 0) or 0
                if current_price > 0 and layer_ratio > 0:
                    max_usdt = self.equity * self.position_config.get('max_position_ratio', 0.3) * self.leverage
                    layer_usdt = max_usdt * layer_ratio
                    _step = float(self.instrument.size_increment)
                    layer_btc = math.floor(layer_usdt / current_price / _step) * _step
                    if layer_btc > 0 and layer_btc < abs(size_diff):
                        self.log.info(
                            f"📊 金字塔仓位调整: {abs(size_diff):.4f} → {layer_btc:.4f} BTC "
                            f"(Layer {len(self._position_layers) + 1}, ratio={layer_ratio:.0%})"
                        )
                        size_diff = layer_btc
                        target_quantity = current_qty + size_diff

                # v4.11: Validate R/R before adding to position (same validation as new positions)
                # Previously, adds bypassed all R/R/S/R validation, allowing entries at resistance
                # with degraded R/R. Now we validate using current price + AI's new SL/TP.
                order_side = OrderSide.BUY if target_side == 'long' else OrderSide.SELL
                validated = self._validate_sltp_for_entry(order_side, confidence)

                if validated is None:
                    # R/R validation failed — reject the add
                    side_cn = '多' if target_side == 'long' else '空'
                    self.log.warning(
                        f"🚫 加仓被拒: R/R 不满足最低要求，维持现有{side_cn}仓 ({current_qty:.4f} BTC)"
                    )
                    self._last_signal_status = {
                        'executed': False,
                        'reason': f'加仓 R/R 不足 (保持 {current_qty:.4f} BTC)',
                        'action_taken': '维持现有仓位',
                    }
                    return

                new_sl_price, new_tp_price, entry_price = validated
                add_qty = abs(size_diff)

                # Pre-submit notional check: make_qty() truncates to size_increment,
                # which can drop notional below Binance $100 minimum (-4164).
                # Check AFTER truncation to avoid submitting SL/TP for a doomed entry.
                _truncated_qty = float(self.instrument.make_qty(add_qty))
                _min_notional_val = float(self.instrument.min_notional) * 1.01 if self.instrument.min_notional else 101.0
                _actual_notional = _truncated_qty * entry_price
                if _actual_notional < _min_notional_val:
                    side_cn = '多' if target_side == 'long' else '空'
                    self.log.warning(
                        f"🚫 加仓被拒: 截断后 notional ${_actual_notional:.0f} < "
                        f"${_min_notional_val:.0f} 最低要求 "
                        f"(qty={_truncated_qty:.4f} BTC × ${entry_price:,.0f})"
                    )
                    self._last_signal_status = {
                        'executed': False,
                        'reason': f'加仓 notional 不足 (${_actual_notional:.0f} < ${_min_notional_val:.0f})',
                        'action_taken': '维持现有仓位',
                    }
                    return

                # Submit add order
                self._submit_order(
                    side=order_side,
                    quantity=add_qty,
                    reduce_only=False,
                )
                self.log.info(
                    f"📈 Adding to {target_side} position: {add_qty:.3f} BTC "
                    f"({current_qty:.3f} → {target_quantity:.3f})"
                )

                # v7.2: Submit INDEPENDENT SL/TP for new layer (don't touch existing layers)
                exit_side = OrderSide.SELL if target_side == 'long' else OrderSide.BUY
                sl_order_id = ""
                tp_order_id = ""

                try:
                    sl_order = self.order_factory.stop_market(
                        instrument_id=self.instrument_id,
                        order_side=exit_side,
                        quantity=self.instrument.make_qty(add_qty),
                        trigger_price=self.instrument.make_price(new_sl_price),
                        trigger_type=TriggerType.LAST_PRICE,
                        reduce_only=True,
                    )
                    self.submit_order(sl_order)
                    sl_order_id = str(sl_order.client_order_id)
                    self.log.info(f"✅ Layer SL submitted: {exit_side.name} {add_qty:.4f} @ ${new_sl_price:,.2f}")
                except Exception as e:
                    self.log.error(f"❌ Failed to submit layer SL: {e}")

                try:
                    tp_order = self.order_factory.limit_if_touched(
                        instrument_id=self.instrument_id,
                        order_side=exit_side,
                        quantity=self.instrument.make_qty(add_qty),
                        price=self.instrument.make_price(new_tp_price),
                        trigger_price=self.instrument.make_price(new_tp_price),
                        trigger_type=TriggerType.LAST_PRICE,
                        reduce_only=True,
                    )
                    self.submit_order(tp_order)
                    tp_order_id = str(tp_order.client_order_id)
                    self.log.info(f"✅ Layer TP submitted: {exit_side.name} {add_qty:.4f} @ ${new_tp_price:,.2f}")
                except Exception as e:
                    self.log.error(f"❌ Failed to submit layer TP: {e}")

                # v7.2: Register new layer (existing layers untouched)
                # v14.1-fix: Only create layer if SL succeeded. When SL fails,
                # _submit_emergency_sl() creates its own layer via _create_layer() —
                # creating one here too would cause duplicate layers tracking the
                # same quantity. Mirrors on_position_opened() pattern (:8067).
                trailing_order_id = ""
                if sl_order_id:
                    self._create_layer(
                        entry_price=entry_price,
                        quantity=add_qty,
                        side=target_side,
                        sl_price=new_sl_price,
                        tp_price=new_tp_price,
                        sl_order_id=sl_order_id,
                        tp_order_id=tp_order_id,
                        confidence=confidence,
                    )

                    # v24.2: Submit trailing stop for new layer (same as position open)
                    risk = abs(entry_price - new_sl_price)
                    atr = self._cached_atr_4h or self._cached_atr_value
                    if risk > 0 and atr and atr > 0:
                        activation_r = self._TRAILING_ACTIVATION_R
                        if target_side == 'long':
                            activation_price = entry_price + (risk * activation_r)
                        else:
                            activation_price = entry_price - (risk * activation_r)

                        trailing_distance = atr * self._TRAILING_ATR_MULTIPLIER
                        trailing_offset_bps = int((trailing_distance / entry_price) * 10000)
                        trailing_offset_bps = max(10, min(1000, trailing_offset_bps))

                        try:
                            trailing_order = self.order_factory.trailing_stop_market(
                                instrument_id=self.instrument_id,
                                order_side=exit_side,
                                quantity=self.instrument.make_qty(add_qty),
                                trigger_price=None,
                                trigger_type=TriggerType.LAST_PRICE,
                                trailing_offset=Decimal(str(trailing_offset_bps)),
                                trailing_offset_type=TrailingOffsetType.BASIS_POINTS,
                                activation_price=self.instrument.make_price(activation_price),
                                reduce_only=True,
                            )
                            self.submit_order(trailing_order)
                            trailing_order_id = str(trailing_order.client_order_id)
                            self.log.info(
                                f"✅ Layer trailing SL submitted: "
                                f"activation @ ${activation_price:,.2f} ({activation_r}R), "
                                f"callback {trailing_offset_bps / 100:.1f}%"
                            )
                            # Update layer with trailing info
                            if self._layer_orders:
                                layer_id = next(reversed(self._layer_orders))
                                self._layer_orders[layer_id]['trailing_order_id'] = trailing_order_id
                                self._layer_orders[layer_id]['trailing_activation_price'] = activation_price
                                self._layer_orders[layer_id]['trailing_offset_bps'] = trailing_offset_bps
                                self._order_to_layer[trailing_order_id] = layer_id
                                self._persist_layer_orders()
                        except Exception as e:
                            self.log.warning(
                                f"⚠️ Layer trailing SL failed (non-critical): {e}"
                            )

                if not sl_order_id:
                    self.log.error("🚨 Layer SL failed - submitting emergency SL")
                    self._submit_emergency_sl(add_qty, target_side, reason="加仓SL提交失败")
                    # Patch emergency layer with actual entry data and TP info
                    # (same pattern as on_position_opened v14.1 :8086-8098)
                    if self._layer_orders:
                        emg_layer_id = max(self._layer_orders.keys(), key=lambda k: int(k.split('_')[-1]) if k.split('_')[-1].isdigit() else 0)
                        emg_layer = self._layer_orders[emg_layer_id]
                        emg_layer['entry_price'] = entry_price
                        emg_layer['confidence'] = confidence
                        if tp_order_id:
                            emg_layer['tp_order_id'] = tp_order_id
                            emg_layer['tp_price'] = new_tp_price
                            self._order_to_layer[tp_order_id] = emg_layer_id
                        self._persist_layer_orders()

                self._sltp_modified_this_cycle = True

                # v4.9: Send scaling notification via Telegram
                if self.telegram_bot and self.enable_telegram:
                    try:
                        with self._state_lock:
                            cached_price = self._cached_current_price
                        current_pos = self._get_current_position_data(current_price=cached_price, from_telegram=False)
                        # Get AI reasoning from pending execution data (set in _execute_signal)
                        exec_data = getattr(self, '_pending_execution_data', None) or {}
                        scaling_msg = self.telegram_bot.format_scaling_notification({
                            'action': 'ADD',
                            'side': target_side,
                            'old_qty': current_qty,
                            'new_qty': target_quantity,
                            'change_qty': add_qty,
                            'current_price': cached_price,
                            'unrealized_pnl': current_pos.get('unrealized_pnl') if current_pos else None,
                            'sl_price': new_sl_price,
                            'tp_price': new_tp_price,
                            'confidence': confidence,
                            'reasoning': exec_data.get('reasoning', ''),
                        })
                        self.telegram_bot.send_message_sync(scaling_msg, broadcast=True)
                    except Exception as e:
                        self.log.debug(f"Failed to send scaling notification: {e}")

                # v6.0: Record pyramid layer
                self._position_layers.append({
                    'entry_price': entry_price,
                    'quantity': add_qty,
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'layer': len(self._position_layers) + 1,
                    'confidence': confidence,
                })
                self._save_position_layers()
                self.log.info(
                    f"📊 Pyramid layer {len(self._position_layers)} recorded "
                    f"(total layers: {len(self._position_layers)})"
                )

                # v4.1: Update signal status - adding to position
                self._last_signal_status = {
                    'executed': True,
                    'reason': '',
                    'action_taken': f'加仓 +{add_qty:.4f} BTC (SL ${new_sl_price:,.0f} TP ${new_tp_price:,.0f})',
                }
            else:
                # Reduce position
                # v3.10: Cancel pending SL/TP before reducing to prevent quantity mismatch
                # Old SL/TP might have larger quantity than reduced position
                cancel_failed = False
                try:
                    open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
                    reduce_only_orders = [o for o in open_orders if o.is_reduce_only]
                    if reduce_only_orders:
                        self.log.info(f"🗑️ Cancelling {len(reduce_only_orders)} SL/TP orders before reduce")
                        for order in reduce_only_orders:
                            try:
                                # v5.13: Track intentionally cancelled order IDs
                                self._intentionally_cancelled_order_ids.add(str(order.client_order_id))
                                self.cancel_order(order)
                            except Exception as e:
                                self.log.warning(f"Failed to cancel order: {e}")
                                cancel_failed = True
                except Exception as e:
                    self.log.error(f"❌ Failed to cancel SL/TP orders: {e}")
                    cancel_failed = True

                if cancel_failed:
                    # v3.10: Warn but continue - reduce is less risky than reversal
                    # Orphan cleanup will handle remaining orders later
                    self.log.warning("⚠️ Some orders failed to cancel, continuing with reduce (orphan cleanup will handle)")

                # v4.4: Re-verify position exists before submitting reduce_only order
                verified_position = self._get_current_position_data()
                if not verified_position:
                    self.log.warning("⚠️ Position no longer exists (likely closed by SL/TP), skipping reduce order")
                    self._last_signal_status = {
                        'executed': False,
                        'reason': '仓位已平仓 (SL/TP 触发)',
                        'action_taken': '',
                    }
                    return
                # Use fresh position quantity for reduce calculation
                fresh_qty = verified_position['quantity']
                actual_reduce = min(abs(size_diff), fresh_qty)  # Can't reduce more than current

                self._submit_order(
                    side=OrderSide.SELL if target_side == 'long' else OrderSide.BUY,
                    quantity=actual_reduce,
                    reduce_only=True,
                )
                self.log.info(
                    f"📉 Reducing {target_side} position: {abs(size_diff):.3f} BTC "
                    f"({current_qty:.3f} → {target_quantity:.3f})"
                )

                # v4.9: Send scaling notification via Telegram
                if self.telegram_bot and self.enable_telegram:
                    try:
                        with self._state_lock:
                            cached_price = self._cached_current_price
                        # Get SL/TP from sltp_state for display
                        reduce_sl = None
                        reduce_tp = None
                        instrument_key = str(self.instrument_id)
                        if instrument_key in self.sltp_state:
                            reduce_sl = self.sltp_state[instrument_key].get('current_sl_price')
                            reduce_tp = self.sltp_state[instrument_key].get('current_tp_price')
                        scaling_msg = self.telegram_bot.format_scaling_notification({
                            'action': 'REDUCE',
                            'side': target_side,
                            'old_qty': current_qty,
                            'new_qty': target_quantity,
                            'change_qty': actual_reduce,
                            'current_price': cached_price,
                            'sl_price': reduce_sl,
                            'tp_price': reduce_tp,
                            'confidence': confidence,
                        })
                        self.telegram_bot.send_message_sync(scaling_msg, broadcast=True)
                    except Exception as e:
                        self.log.debug(f"Failed to send scaling notification: {e}")

                # v4.10: Recreate SL/TP for remaining position after reduce
                # All SL/TP were cancelled above, so we must recreate from sltp_state
                remaining_qty = fresh_qty - actual_reduce
                if remaining_qty > 0.0001:
                    self._recreate_sltp_after_reduce(remaining_qty, target_side)
                    self.log.info(
                        f"📝 Recreated SL/TP for remaining {remaining_qty:.4f} BTC"
                    )

                # v4.1: Update signal status - reducing position
                self._last_signal_status = {
                    'executed': True,
                    'reason': '',
                    'action_taken': f'减仓 -{abs(size_diff):.4f} BTC',
                }

        # Opposite direction - reverse position
        elif self.allow_reversals:
            # Check if high confidence required for reversal
            if self.require_high_conf_reversal and confidence != 'HIGH':
                self.log.warning(
                    f"🔒 Reversal requires HIGH confidence, got {confidence}. "
                    f"Keeping {current_side} position."
                )
                # v4.1: Update signal status - reversal blocked
                side_cn = '多' if current_side == 'long' else '空'
                self._last_signal_status = {
                    'executed': False,
                    'reason': f'反转需HIGH信心 (当前{confidence})',
                    'action_taken': f'保持{side_cn}仓',
                }
                return

            self.log.info(f"🔄 Reversing position: {current_side} → {target_side}")

            # v4.4: Re-verify position exists before reversal
            verified_position = self._get_current_position_data()
            if not verified_position:
                self.log.warning("⚠️ Position no longer exists (likely closed by SL/TP), skipping reversal")
                self._last_signal_status = {
                    'executed': False,
                    'reason': '仓位已平仓 (SL/TP 触发)',
                    'action_taken': '',
                }
                return
            # Update with fresh position data
            current_qty = verified_position['quantity']

            # v3.10: Cancel all pending orders BEFORE reversing to prevent -2022 ReduceOnly rejection
            # Old position's SL/TP orders are reduce_only=True, they'll be rejected if position closes first
            try:
                open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
                if open_orders:
                    self.log.info(f"🗑️ Cancelling {len(open_orders)} pending orders before reversal")
                    self.cancel_all_orders(self.instrument_id)
            except Exception as e:
                # v3.10: ABORT reversal if cancel fails - continuing would cause -2022 errors
                self.log.error(f"❌ Failed to cancel pending orders, aborting reversal: {e}")
                self._last_signal_status = {
                    'executed': False,
                    'reason': f'取消挂单失败: {str(e)}',
                    'action_taken': '中止反转',
                }
                return

            # v3.18: Event-driven two-phase commit for reversal
            # Phase 1: Store pending reversal state and submit close order
            # Phase 2: on_position_closed will detect pending reversal and open new position
            # This prevents race condition where new position opens before old one closes
            old_side_cn = '多' if current_side == 'long' else '空'
            new_side_cn = '多' if target_side == 'long' else '空'

            self._pending_reversal = {
                'target_side': target_side,
                'target_quantity': target_quantity,
                'old_side': current_side,
                'submitted_at': datetime.now(timezone.utc),
            }
            self.log.info(
                f"📋 Reversal Phase 1: Stored pending reversal state "
                f"({old_side_cn}→{new_side_cn}, qty={target_quantity:.4f})"
            )

            # Close current position (Phase 1)
            self._submit_order(
                side=OrderSide.SELL if current_side == 'long' else OrderSide.BUY,
                quantity=current_qty,
                reduce_only=True,
            )

            # v3.18: Do NOT open new position here - wait for on_position_closed
            # Update signal status to indicate reversal in progress
            self._last_signal_status = {
                'executed': True,
                'reason': '',
                'action_taken': f'反转中: {old_side_cn}→{new_side_cn} (等待平仓)',
            }
            self.log.info(
                f"⏳ Reversal Phase 1 complete: Close order submitted, "
                f"waiting for on_position_closed to open new {target_side} position"
            )

        else:
            self.log.warning(
                f"⚠️ Signal suggests {target_side} but have {current_side} position. "
                f"Reversals disabled."
            )
            # v4.1: Update signal status - reversal disabled
            current_cn = '多' if current_side == 'long' else '空'
            target_cn = '多' if target_side == 'long' else '空'
            self._last_signal_status = {
                'executed': False,
                'reason': f'禁止反转 (持有{current_cn}仓)',
                'action_taken': '',
            }

    def _validate_sltp_for_entry(
        self,
        side: OrderSide,
        confidence: str,
    ) -> Optional[Tuple[float, float, float]]:
        """
        v11.0: Calculate mechanical SL/TP for entry using ATR-based formula.

        Architecture (Lopez de Prado Triple Barrier):
        - AI provides direction + confidence + risk_appetite (qualitative)
        - This function computes SL/TP mechanically from ATR (quantitative)
        - R/R is guaranteed by construction (rr_target >= 1.5)

        Fallback: only when ATR=0 (startup period with no data),
        uses default_sl_pct / default_tp_pct as percentage-based floor.

        Parameters
        ----------
        side : OrderSide
            Order side (BUY or SELL)
        confidence : str
            Signal confidence level (HIGH/MEDIUM/LOW)

        Returns
        -------
        Optional[Tuple[float, float, float]]
            (stop_loss_price, take_profit_price, entry_price) if valid,
            None if calculation fails
        """
        if not self.latest_signal_data or not self.latest_technical_data:
            self.log.warning("⚠️ No signal/technical data for SL/TP validation")
            return None

        # Get current real-time price (same priority chain as _submit_bracket_order)
        entry_price: Optional[float] = None

        if self.binance_account:
            try:
                realtime_price = self.binance_account.get_realtime_price('BTCUSDT')
                if realtime_price and realtime_price > 0:
                    entry_price = realtime_price
            except Exception as e:
                self.log.debug(f"Real-time price fetch is best-effort; cached price used as fallback: {e}")
                pass  # Real-time price fetch is best-effort; cached price used as fallback

        if entry_price is None and self.latest_price_data and self.latest_price_data.get('price'):
            entry_price = float(self.latest_price_data['price'])

        if entry_price is None and hasattr(self.indicator_manager, "recent_bars"):
            recent_bars = self.indicator_manager.recent_bars
            if recent_bars:
                entry_price = float(recent_bars[-1].close)

        if entry_price is None:
            cache_bars = self.cache.bars(self.bar_type)
            if cache_bars:
                entry_price = float(cache_bars[-1].close)

        if entry_price is None or entry_price <= 0:
            self.log.error("❌ Cannot determine price for SL/TP validation")
            return None

        atr_value = self._cached_atr_value or 0.0
        atr_4h_value = getattr(self, '_cached_atr_4h', 0.0) or 0.0  # v39.0
        is_long = side == OrderSide.BUY

        # v11.0: Detect counter-trend
        is_counter = False
        if self.latest_technical_data:
            is_counter = _is_counter_trend(is_long, self.latest_technical_data)

        # v11.0: Get risk_appetite from AI signal
        risk_appetite = "NORMAL"
        if self.latest_signal_data:
            risk_appetite = self.latest_signal_data.get("risk_appetite", "NORMAL")

        # v48.0: Use DCA fixed-% SL/TP when DCA mode enabled
        _dca_enabled = getattr(self, '_dca_config', {}).get('enabled', False)
        if _dca_enabled and self._strategy_mode == 'mechanical':
            _dca_tp = getattr(self, '_dca_config', {}).get('tp_pct', 0.025)
            _dca_sl = getattr(self, '_dca_config', {}).get('sl_pct', 0.06)
            success, stop_loss_price, tp_price, method = calculate_dca_sltp(
                real_avg_price=entry_price,
                virtual_avg_price=entry_price,
                side=side.name,
                tp_pct=_dca_tp,
                sl_pct=_dca_sl,
            )
        else:
            # Fallback: ATR-based SL/TP
            success, stop_loss_price, tp_price, method = calculate_mechanical_sltp(
                entry_price=entry_price,
                side=side.name,
                atr_value=atr_value,
                confidence=confidence,
                risk_appetite=risk_appetite,
                is_counter_trend=is_counter,
                atr_4h=atr_4h_value,
            )

        # v11.5: Capture entry-time data for SL/TP optimization analysis
        self._entry_atr_value = atr_value
        self._entry_is_counter_trend = is_counter
        self._entry_risk_appetite = risk_appetite
        # Compute effective SL ATR multiplier from actual prices
        if success and atr_value > 0:
            sl_dist = abs(entry_price - stop_loss_price)
            self._entry_sl_atr_multiplier = round(sl_dist / atr_value, 2)
        else:
            self._entry_sl_atr_multiplier = 0.0
        # Trend context
        if self.latest_technical_data:
            self._entry_trend_direction = self.latest_technical_data.get('trend_direction', '')
            self._entry_adx = self.latest_technical_data.get('adx', 0.0)

        if not success:
            # Fallback: ATR=0 during startup — use percentage-based defaults
            self.log.warning(f"⚠️ Mechanical SL/TP failed ({method}), trying percentage fallback")
            sl_pct = get_default_sl_pct()
            tp_pct = get_default_tp_pct_buy()
            # Enforce R/R >= min_rr_ratio even in fallback path
            min_rr = get_min_rr_ratio()
            if self._entry_is_counter_trend:
                ct_mult = get_counter_trend_rr_multiplier()
                min_rr *= ct_mult
            if sl_pct > 0 and tp_pct / sl_pct < min_rr:
                tp_pct = sl_pct * min_rr
                self.log.info(f"📍 Fallback TP adjusted to {tp_pct:.1%} to meet R/R >= {min_rr:.2f}")
            if is_long:
                stop_loss_price = entry_price * (1 - sl_pct)
                tp_price = entry_price * (1 + tp_pct)
            else:
                stop_loss_price = entry_price * (1 + sl_pct)
                tp_price = entry_price * (1 - tp_pct)
            method = f"pct_fallback|sl={sl_pct:.1%}|tp={tp_pct:.1%}"
            self.log.info(f"📍 Percentage fallback SL/TP: {method}")

        # Compute R/R for logging
        if is_long:
            rr = (tp_price - entry_price) / (entry_price - stop_loss_price) if entry_price > stop_loss_price else 0
        else:
            rr = (entry_price - tp_price) / (stop_loss_price - entry_price) if stop_loss_price > entry_price else 0
        self.log.info(
            f"✅ SL/TP validated (v11.0 mechanical): Price=${entry_price:,.2f} "
            f"SL=${stop_loss_price:,.2f} TP=${tp_price:,.2f} R/R={rr:.2f}:1 "
            f"[{method}]"
        )

        return (stop_loss_price, tp_price, entry_price)

    def _open_new_position(self, side: str, quantity: float):
        """
        Open new position using two-phase order submission.

        v4.17: LIMIT entry at validated price, SL/TP submitted after fill.
        - Entry order (LIMIT at validated entry_price)
        - Stop Loss order (STOP_MARKET, submitted in on_position_opened)
        - Take Profit order (LIMIT_IF_TOUCHED, submitted in on_position_opened)
        """
        order_side = OrderSide.BUY if side == 'long' else OrderSide.SELL

        # v6.6: Funding rate gate — same threshold as pyramiding (0.03% default).
        # High FR means significant cost every 8h. Check direction:
        # LONG + positive FR → paying; SHORT + negative FR → paying.
        # Mechanical mode: FR is already factored into anticipatory scoring (Order Flow
        # dimension vote), so the hard gate is skipped to avoid double-penalizing.
        # FR counter still increments for /status display.
        fr_data = (self.latest_derivatives_data or {}).get('funding_rate', {})
        fr_pct = fr_data.get('current_pct', fr_data.get('settled_pct', 0)) or 0
        try:
            fr_pct = float(fr_pct)
        except (ValueError, TypeError) as e:
            self.log.debug(f"Using default value, original error: {e}")
            fr_pct = 0
        fr_abs = abs(fr_pct / 100) if abs(fr_pct) > 0.1 else abs(fr_pct)  # Handle % vs decimal
        paying_funding = (side == 'long' and fr_pct > 0) or (side == 'short' and fr_pct < 0)
        max_fr = getattr(self, 'pyramiding_max_funding_rate', 0.0003)
        _is_mechanical = getattr(self, '_strategy_mode', 'ai') == 'mechanical'
        if paying_funding and fr_abs > max_fr * 3:
            # Only block at 3× the pyramiding threshold (0.09%) — severe FR pressure
            # v21.0: Track consecutive FR blocks in same direction
            if self._fr_block_direction == side:
                self._fr_consecutive_blocks += 1
            else:
                self._fr_consecutive_blocks = 1
                self._fr_block_direction = side
            self.log.info(
                f"📊 FR consecutive blocks: {self._fr_consecutive_blocks}× {side.upper()}"
            )
            # Mechanical mode: FR already in net_raw, log but don't block
            self.log.info(
                f"ℹ️ FR gate bypassed (mechanical): FR {fr_pct:.5f}% "
                f"(already factored in anticipatory scoring)"
            )
        elif paying_funding and fr_abs > max_fr:
            self.log.info(
                f"⚠️ Funding Rate elevated ({fr_pct:.5f}%) for {side.upper()} entry — proceeding with caution"
            )

        # Submit bracket order with SL/TP
        self._submit_bracket_order(
            side=order_side,
            quantity=quantity,
        )

        self.log.info(f"🚀 Opening {side} position: {quantity:.3f} BTC (with bracket SL/TP)")

        # v21.0: Reset FR consecutive block counter on successful entry
        if self._fr_consecutive_blocks > 0:
            self.log.info(
                f"✅ FR block counter reset (was {self._fr_consecutive_blocks}× {self._fr_block_direction.upper()})"
            )
            self._fr_consecutive_blocks = 0
            self._fr_block_direction = ""

        # v23.0: Reset Entry Timing REJECT counter on successful entry
        if self._et_consecutive_rejects > 0:
            self.log.info(
                f"✅ Entry Timing REJECT counter reset (was {self._et_consecutive_rejects}×)"
            )
            self._et_consecutive_rejects = 0

        # v6.0: Initialize pyramid layer tracking for new position
        with self._state_lock:
            entry_price = self._cached_current_price or 0
        self._position_layers = [{
            'entry_price': entry_price,
            'quantity': quantity,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'layer': 1,
            'confidence': self._position_entry_confidence or 'MEDIUM',
        }]
        self._save_position_layers()

        # v4.1: Update signal status - new position opened
        side_cn = '多' if side == 'long' else '空'
        self._last_signal_status = {
            'executed': True,
            'reason': '',
            'action_taken': f'开{side_cn}仓 {quantity:.4f} BTC',
        }

    def _adjust_tp_for_fill_price(
        self,
        tp_price: float,
        sl_price: float,
        fill_price: float,
        side: str,
    ) -> tuple:
        """
        v4.16: Pre-adjust TP price using actual fill price before order submission.

        Replaces the old cancel+resubmit approach in _validate_and_adjust_rr_post_fill
        for new position entries, eliminating the async race condition that caused
        -2022 ReduceOnly rejection on Binance.

        Returns
        -------
        tuple of (adjusted_tp_price, was_adjusted: bool)
        """
        try:
            min_rr = get_min_rr_ratio()

            is_long = side.upper() in ('LONG', 'BUY')

            if is_long:
                risk = fill_price - sl_price
                reward = tp_price - fill_price
            else:
                risk = sl_price - fill_price
                reward = fill_price - tp_price

            if risk <= 0:
                self.log.warning(
                    f"⚠️ TP fill-adjust: SL on wrong side "
                    f"(fill=${fill_price:,.2f}, SL=${sl_price:,.2f}, side={side})"
                )
                return tp_price, False

            actual_rr = reward / risk

            if actual_rr >= min_rr:
                self.log.info(
                    f"✅ Pre-submit R/R check: {actual_rr:.2f}:1 >= {min_rr}:1 "
                    f"(fill=${fill_price:,.2f}) — TP unchanged"
                )
                return tp_price, False

            # R/R below minimum — adjust TP
            if is_long:
                new_tp = fill_price + (risk * min_rr)
            else:
                new_tp = fill_price - (risk * min_rr)

            self.log.warning(
                f"⚠️ Fill slippage degraded R/R: {actual_rr:.2f}:1 < {min_rr}:1 "
                f"(fill=${fill_price:,.2f}). Adjusting TP: ${tp_price:,.2f} → ${new_tp:,.2f}"
            )
            return new_tp, True

        except Exception as e:
            self.log.error(f"❌ _adjust_tp_for_fill_price failed: {e}")
            return tp_price, False

    def _validate_and_adjust_rr_post_fill(
        self,
        instrument_key: str,
        fill_price: float,
        side: str,
    ):
        """
        v4.9: Post-fill R/R validation and TP adjustment.

        Problem: SL/TP are calculated using estimated price, but fill price may differ
        (v4.17 LIMIT fills at entry_price or better; legacy add/reversal paths may vary).
        This can degrade R/R below the 1.5:1 minimum.

        Solution: After fill, recalculate R/R with actual fill price. If below minimum,
        cancel existing TP and submit a new one that restores R/R >= min_rr_ratio.

        Parameters
        ----------
        instrument_key : str
            Instrument key in sltp_state
        fill_price : float
            Actual fill price from PositionOpened event
        side : str
            Position side ('LONG' or 'SHORT')
        """
        try:
            state = self.sltp_state.get(instrument_key)
            if not state:
                return

            sl_price = state.get("current_sl_price")
            tp_price = state.get("current_tp_price")

            if not sl_price or not tp_price or fill_price <= 0:
                return

            is_long = side.upper() == 'LONG'
            min_rr = get_min_rr_ratio()

            # Calculate R/R with actual fill price
            if is_long:
                risk = fill_price - sl_price
                reward = tp_price - fill_price
            else:
                risk = sl_price - fill_price
                reward = fill_price - tp_price

            if risk <= 0:
                self.log.warning(
                    f"⚠️ Post-fill R/R check: SL on wrong side "
                    f"(fill=${fill_price:,.2f}, SL=${sl_price:,.2f}, side={side})"
                )
                return

            actual_rr = reward / risk

            if actual_rr >= min_rr:
                self.log.info(
                    f"✅ Post-fill R/R check: {actual_rr:.2f}:1 >= {min_rr}:1 "
                    f"(fill=${fill_price:,.2f})"
                )
                return

            # R/R below minimum — need to adjust TP
            self.log.warning(
                f"⚠️ Post-fill R/R degraded: {actual_rr:.2f}:1 < {min_rr}:1 "
                f"(estimated entry vs fill=${fill_price:,.2f}, diff=${fill_price - state.get('entry_price', fill_price):+.2f})"
            )

            # Calculate new TP to restore R/R
            if is_long:
                new_tp = fill_price + (risk * min_rr)
            else:
                new_tp = fill_price - (risk * min_rr)

            self.log.info(
                f"🔄 Adjusting TP: ${tp_price:,.2f} → ${new_tp:,.2f} "
                f"(restoring R/R to {min_rr}:1)"
            )

            # Find and cancel existing TP order, submit new one
            open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
            tp_cancelled = False

            for order in open_orders:
                if order.is_reduce_only and order.order_type == OrderType.LIMIT_IF_TOUCHED:
                    try:
                        self.cancel_order(order)
                        tp_cancelled = True
                        self.log.debug(f"🗑️ Cancelled old TP: {str(order.client_order_id)[:8]}")
                    except Exception as e:
                        self.log.warning(f"Failed to cancel old TP: {e}")

            # Submit adjusted TP order
            # v6.6: LIMIT_IF_TOUCHED → Binance TAKE_PROFIT (Algo API, position-linked, auto-cancel)
            tp_side = OrderSide.SELL if is_long else OrderSide.BUY
            quantity = state.get("quantity", 0)
            if quantity > 0:
                new_tp_order = self.order_factory.limit_if_touched(
                    instrument_id=self.instrument_id,
                    order_side=tp_side,
                    quantity=self.instrument.make_qty(quantity),
                    price=self.instrument.make_price(new_tp),
                    trigger_price=self.instrument.make_price(new_tp),
                    trigger_type=TriggerType.LAST_PRICE,
                    reduce_only=True,
                )
                self.submit_order(new_tp_order)

                # Update trailing stop state
                state["current_tp_price"] = new_tp

                self.log.info(
                    f"✅ TP adjusted post-fill: ${tp_price:,.2f} → ${new_tp:,.2f} "
                    f"(R/R restored to {min_rr}:1)"
                )

                # v14.0: Log TP adjustment for web dashboard
                self._log_sltp_adjustment({
                    'type': 'post_fill_tp_adjust',
                    'side': side,
                    'old_tp': tp_price,
                    'new_tp': new_tp,
                    'fill_price': fill_price,
                    'old_rr': round(actual_rr, 2),
                    'new_rr': min_rr,
                })

                # Send Telegram alert
                if self.telegram_bot and self.enable_telegram:
                    try:
                        alert_msg = (
                            f"🔄 *TP 自动调整 (成交后)*\n"
                            f"━━━━━━━━━━━━━━━━━━\n"
                            f"原因: 实际成交价 ${fill_price:,.2f} 导致 R/R 降至 {actual_rr:.2f}:1\n"
                            f"旧 TP: ${tp_price:,.2f}\n"
                            f"新 TP: ${new_tp:,.2f}\n"
                            f"R/R: {actual_rr:.2f}:1 → {min_rr}:1\n"
                            f"SL: ${sl_price:,.2f} (不变)\n\n"
                            f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
                        )
                        self.telegram_bot.send_message_sync(alert_msg)
                    except Exception as e:
                        self.log.debug(f"Telegram notification failure is non-critical: {e}")
                        pass  # Telegram notification failure is non-critical
            else:
                self.log.warning("⚠️ Cannot adjust TP: quantity unknown")

        except Exception as e:
            self.log.error(f"❌ Post-fill R/R validation failed: {e}")

    # ========================== v24.0 Trailing Stop Methods ==========================

    # Binance Futures trailing stop callback rate limits (basis points)
    # Source: https://www.binance.com/en/support/faq/360042299292
    _TRAILING_MIN_BPS = 10     # 0.1% minimum
    _TRAILING_MAX_BPS = 1000   # 10.0% maximum (Futures; Spot is 20%)
    # ATR multiplier for trailing distance calculation (v43.0: 4H ATR source)
    _TRAILING_ATR_MULTIPLIER = 0.6

    def _submit_trailing_stop(
        self,
        layer_id: str,
        quantity: float,
        side: str,
        activation_price: float,
        trailing_offset_bps: int,
    ) -> str | None:
        """
        Submit a Binance native TRAILING_STOP_MARKET order for a layer.

        v24.0: Server-side trailing stop — Binance handles real-time trailing
        after activation. Survives bot restarts and network outages.

        Parameters
        ----------
        layer_id : str
            Layer identifier for logging
        quantity : float
            Position quantity in BTC for this layer
        side : str
            Position side ('long' or 'short')
        activation_price : float
            Price at which trailing activates (current price when profit >= 1R)
        trailing_offset_bps : int
            Callback rate in basis points (100 = 1%). Clamped to Binance limits [10, 500].

        Returns
        -------
        str | None
            Client order ID string if successful, None on failure.
        """
        exit_side = OrderSide.SELL if side.lower() == 'long' else OrderSide.BUY

        # Clamp to Binance limits
        trailing_offset_bps = max(self._TRAILING_MIN_BPS, min(self._TRAILING_MAX_BPS, trailing_offset_bps))

        try:
            trailing_order = self.order_factory.trailing_stop_market(
                instrument_id=self.instrument_id,
                order_side=exit_side,
                quantity=self.instrument.make_qty(quantity),
                trigger_price=None,
                trigger_type=TriggerType.LAST_PRICE,
                trailing_offset=Decimal(str(trailing_offset_bps)),
                trailing_offset_type=TrailingOffsetType.BASIS_POINTS,
                activation_price=self.instrument.make_price(activation_price),
                reduce_only=True,
            )
            self.submit_order(trailing_order)
            order_id = str(trailing_order.client_order_id)
            self.log.info(
                f"✅ Trailing SL submitted for {layer_id}: "
                f"{exit_side.name} {quantity:.4f} BTC, "
                f"callback {trailing_offset_bps / 100:.1f}%, "
                f"activation @ ${activation_price:,.2f}"
            )
            return order_id
        except Exception as e:
            self.log.error(f"❌ Failed to submit trailing stop for {layer_id}: {e}")
            return None

    def _backfill_trailing_for_existing_layers(
        self, position_side: str, entry_price: float
    ):
        """
        v24.2: Submit TRAILING_STOP_MARKET for layers missing trailing orders.

        Called during startup recovery to backfill trailing protection for
        positions opened before trailing stop support was added. Only layers
        with a fixed SL and NO trailing order are eligible.

        Non-critical: failures are logged but don't affect fixed SL protection.
        """
        if not self._layer_orders:
            return

        atr = self._cached_atr_4h or self._cached_atr_value
        if not atr or atr <= 0:
            self.log.info(
                "ℹ️ Trailing backfill skipped: ATR not yet available "
                "(will be attempted on next on_timer cycle)"
            )
            return

        backfilled = 0
        for layer_id, layer in self._layer_orders.items():
            # Only backfill layers that have fixed SL but no trailing
            sl_id = layer.get('sl_order_id', '')
            trailing_id = layer.get('trailing_order_id', '')
            if not sl_id or trailing_id:
                continue  # No SL to base on, or already has trailing

            sl_price = layer.get('sl_price', 0)
            layer_entry = layer.get('entry_price', entry_price)
            layer_qty = layer.get('quantity', 0)
            layer_side = layer.get('side', position_side)

            if sl_price <= 0 or layer_entry <= 0 or layer_qty <= 0:
                continue

            risk = abs(layer_entry - sl_price)
            if risk <= 0:
                continue

            # Calculate activation price (same formula as on_position_opened)
            activation_r = self._TRAILING_ACTIVATION_R
            if layer_side == 'long':
                activation_price = layer_entry + (risk * activation_r)
            else:
                activation_price = layer_entry - (risk * activation_r)

            # Calculate trailing offset
            trailing_distance = atr * self._TRAILING_ATR_MULTIPLIER
            trailing_offset_bps = int((trailing_distance / layer_entry) * 10000)
            trailing_offset_bps = max(
                self._TRAILING_MIN_BPS,
                min(self._TRAILING_MAX_BPS, trailing_offset_bps),
            )

            # Submit trailing stop
            new_trailing_id = self._submit_trailing_stop(
                layer_id=layer_id,
                quantity=layer_qty,
                side=layer_side,
                activation_price=activation_price,
                trailing_offset_bps=trailing_offset_bps,
            )

            if new_trailing_id:
                layer['trailing_order_id'] = new_trailing_id
                layer['trailing_offset_bps'] = trailing_offset_bps
                layer['trailing_activation_price'] = activation_price
                self._order_to_layer[new_trailing_id] = layer_id
                backfilled += 1
                self.log.info(
                    f"📌 Backfilled trailing for {layer_id}: "
                    f"activation @ ${activation_price:,.2f} ({activation_r}R), "
                    f"callback {trailing_offset_bps / 100:.1f}%"
                )

        if backfilled > 0:
            self._persist_layer_orders()
            self.log.info(
                f"✅ Trailing backfill complete: {backfilled}/{len(self._layer_orders)} layers upgraded"
            )
        else:
            self.log.info("ℹ️ Trailing backfill: all layers already have trailing or ineligible")

    # ========================== v4.0 Order Safety Methods ==========================

