"""
Position Manager Mixin

Extracted from ai_strategy.py for code organization.
Contains time barrier, cooldown logic, layer order system (v7.2),
pyramiding validation, position close/reduce, order submission
primitives, reflection processing, and bracket order submission.
"""

import os
import json
import math
import time
from typing import Dict, Any, List
from datetime import datetime, timedelta, timezone

from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce, TriggerType
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.position import Position

from strategy.trading_logic import (
    _is_counter_trend,
    evaluate_trade,
    get_time_barrier_config,
    get_min_notional_usdt,
    get_min_notional_safety_margin,
)


class PositionManagerMixin:
    """Mixin providing position management methods for AITradingStrategy."""

    def _check_time_barrier(self) -> bool:
        """
        v11.0-simple: Time Barrier — enforce maximum holding period.

        Lopez de Prado (2018) Triple Barrier: vertical barrier = time expiry.
        Always market close when expired (no breakeven option).
        Trend: 12h max | Counter-trend: 6h max.

        Called every on_timer() cycle (highest priority in position management).

        Returns
        -------
        bool
            True if time barrier triggered a close, False otherwise.
        """
        try:
            tb_cfg = get_time_barrier_config()
            if not tb_cfg.get('enabled', True):
                return False

            max_hours_trend = tb_cfg.get('max_holding_hours_trend', 12)
            max_hours_counter = tb_cfg.get('max_holding_hours_counter', 6)

            current_position = self._get_current_position_data()
            if not current_position:
                return False

            position_side = current_position.get('side', '').lower()
            if position_side not in ('long', 'short'):
                return False

            # Use duration_minutes from position data (restart-safe)
            duration_minutes = current_position.get('duration_minutes', 0)
            if duration_minutes <= 0:
                return False

            hours_held = duration_minutes / 60.0
            is_long = position_side == 'long'

            # Determine max hours based on trend/counter-trend
            is_counter = False
            if self.latest_technical_data:
                is_counter = _is_counter_trend(is_long, self.latest_technical_data)
            max_hours = max_hours_counter if is_counter else max_hours_trend

            if hours_held < max_hours:
                return False  # Not expired yet

            # Time barrier triggered
            instrument_key = str(self.instrument_id)
            state = self.sltp_state.get(instrument_key, {})
            entry_price = state.get('entry_price', 0)
            current_price = self._cached_current_price or 0
            current_sl = state.get('current_sl_price', 0)

            pnl = 0.0
            if entry_price > 0 and current_price > 0:
                pnl = (current_price - entry_price) if is_long else (entry_price - current_price)

            trend_type = "counter-trend" if is_counter else "trend"
            position_qty = current_position.get('quantity', 0)

            # v6.6: If SL is already in profit territory,
            # tighten SL aggressively instead of market close (better execution).
            sl_in_profit = False
            if entry_price > 0 and current_sl > 0:
                if is_long and current_sl > entry_price:
                    sl_in_profit = True
                elif not is_long and current_sl < entry_price:
                    sl_in_profit = True

            if sl_in_profit and pnl > 0:
                # SL already locks profit. Tighten it to near current price.
                atr = self._cached_atr_value or 0
                tight_distance = max(atr * 0.5, current_price * 0.001)  # 0.5×ATR or 0.1%
                if is_long:
                    tight_sl = current_price - tight_distance
                    tight_sl = max(tight_sl, current_sl)  # Never move SL backward
                else:
                    tight_sl = current_price + tight_distance
                    tight_sl = min(tight_sl, current_sl)

                self.log.warning(
                    f"⏰ TIME BARRIER: {hours_held:.1f}h (max={max_hours}h {trend_type}) | "
                    f"P&L ${pnl:+,.2f} | SL already in profit → tightening all layers to ${tight_sl:,.2f}"
                )

                # v7.2: Tighten each layer's SL to tight_sl
                exit_side = OrderSide.SELL if is_long else OrderSide.BUY
                for layer_id, layer in list(self._layer_orders.items()):
                    old_layer_sl = layer.get('sl_price', 0)
                    # Only tighten if new SL is better than current
                    should_update = (is_long and tight_sl > old_layer_sl) or (not is_long and tight_sl < old_layer_sl)
                    if not should_update:
                        continue
                    try:
                        old_sl_id = layer.get('sl_order_id', '')
                        if old_sl_id:
                            old_order = self.cache.order(ClientOrderId(old_sl_id))
                            if old_order and old_order.is_open:
                                self._intentionally_cancelled_order_ids.add(old_sl_id)
                                self.cancel_order(old_order)
                        new_sl_order = self.order_factory.stop_market(
                            instrument_id=self.instrument_id,
                            order_side=exit_side,
                            quantity=self.instrument.make_qty(layer['quantity']),
                            trigger_price=self.instrument.make_price(tight_sl),
                            trigger_type=TriggerType.LAST_PRICE,
                            reduce_only=True,
                        )
                        self.submit_order(new_sl_order)
                        self._update_layer_order_id(layer_id, 'sl', str(new_sl_order.client_order_id))
                        layer['sl_price'] = tight_sl
                    except Exception as e:
                        self.log.error(f"❌ Time barrier SL tighten failed for {layer_id}: {e}")
                self._update_aggregate_sltp_state()
                self._persist_layer_orders()
                self._sltp_modified_this_cycle = True
                action_msg = f"SL 收紧至 ${tight_sl:,.2f} (保留利润)"
            else:
                # SL not in profit — market close immediately
                self.log.warning(
                    f"⏰ TIME BARRIER: {hours_held:.1f}h held (max={max_hours}h {trend_type}) | "
                    f"P&L ${pnl:+,.2f} → market close"
                )
                # v14.1: Set forced close reason so on_position_closed() shows correct reason
                self._forced_close_reason = (
                    'TIME_BARRIER',
                    f'⏰ 时间屏障 ({hours_held:.1f}h > {max_hours}h {trend_type})',
                )
                self._emergency_market_close(
                    quantity=position_qty,
                    position_side=position_side,
                    reason=f"Time barrier: {hours_held:.1f}h exceeded max {max_hours}h ({trend_type})",
                )
                action_msg = "市价平仓"

            # Telegram notification
            if self.telegram_bot and self.enable_telegram:
                try:
                    pos_cn = self.telegram_bot.side_to_cn(position_side, 'position')
                    self.telegram_bot.send_message_sync(
                        f"⏰ Time Barrier 触发\n"
                        f"持仓: {pos_cn} {hours_held:.1f}h (上限 {max_hours}h {trend_type})\n"
                        f"P&L: ${pnl:+,.2f}\n"
                        f"动作: {action_msg}"
                    )
                except Exception as e:
                    self.log.debug(f"Telegram notification failure is non-critical: {e}")
                    pass  # Telegram notification failure is non-critical

            return True  # Time barrier triggered

        except Exception as e:
            self.log.warning(f"⚠️ Time barrier check error (position may exceed hold limit): {e}")
            return False

    # =========================================================================
    # v6.0: Cooldown, Pyramiding & Confidence Management
    # =========================================================================

    def _activate_stoploss_cooldown(self, exit_price: float, entry_price: float, side: str):
        """
        v6.0: Activate per-stop cooldown after a stop-loss exit.

        Sets cooldown duration based on default candle count.
        Stop-type refinement (noise/reversal/volatility) happens in next on_timer
        when new bar data is available.
        """

        timer_interval = self.config.timer_interval_sec  # seconds per timer cycle
        candles = self.cooldown_per_stoploss_candles  # default cooldown

        # Check if this is a volatility-driven stop (ATR > 2x normal)
        atr = self._cached_atr_value
        normal_atr = self.risk_controller.metrics.normal_atr
        if normal_atr > 0 and atr > 0 and (atr / normal_atr) >= 2.0:
            candles = self.cooldown_volatility_stop_candles
            stop_type = "volatility"
            self.log.warning(
                f"⏸️ Volatility stop detected (ATR {atr / normal_atr:.1f}x normal) → "
                f"cooldown {candles} candles ({candles * timer_interval // 60:.0f} min)"
            )
        else:
            stop_type = "default"
            self.log.info(
                f"⏸️ Stop-loss cooldown activated: {candles} candles ({candles * timer_interval // 60:.0f} min)"
            )

        cooldown_seconds = candles * timer_interval
        self._stoploss_cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=cooldown_seconds)
        self._stoploss_cooldown_type = stop_type
        self._last_stoploss_price = exit_price
        self._last_stoploss_time = datetime.now(timezone.utc)
        self._last_stoploss_side = side

        # Telegram notification for cooldown activation (private chat — operational info)
        if self.telegram_bot and self.enable_telegram and self.telegram_notify_errors:
            try:
                cooldown_min = cooldown_seconds // 60
                side_cn = self.telegram_bot.side_to_cn(side, 'position')
                cooldown_msg = (
                    f"⏸️ *止损冷静期激活*\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"📊 {side_cn} 止损 @ ${exit_price:,.2f}\n"
                    f"⏱️ 冷静期: {cooldown_min} 分钟 ({stop_type})\n"
                    f"🔄 预计恢复: {self._stoploss_cooldown_until.strftime('%H:%M')} UTC\n\n"
                    f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
                )
                self.telegram_bot.send_message_sync(cooldown_msg)
            except Exception as e:
                self.log.debug(f"Cooldown notification failed (non-critical): {e}")

    def _has_market_changed(self) -> tuple:
        """
        v15.0: Check if market conditions changed enough to warrant AI re-analysis.
        Returns (should_analyze: bool, reason: str).

        3 checks: price movement >0.2%, ATR regime shift >15%, position state change.
        Watchdog timer in on_timer() forces analysis after consecutive skips.
        """
        if self._last_analysis_price is None:
            return True, "first_analysis"

        # v2 fix (BUG-P0-3): _cached_current_price is float=0.0, never None
        current_price = self._cached_current_price
        if current_price <= 0:
            return True, "no_price_data"

        # Check 1: Price moved > 0.2% from last analysis
        price_change_pct = abs(current_price - self._last_analysis_price) / self._last_analysis_price
        if price_change_pct > 0.002:
            return True, f"price_moved_{price_change_pct:.4f}"

        # Check 2: ATR regime shifted (ATR changed by 15%+)
        current_atr = self._cached_atr_value
        if current_atr > 0 and self._last_analysis_atr and self._last_analysis_atr > 0:
            atr_change_pct = abs(current_atr - self._last_analysis_atr) / self._last_analysis_atr
            if atr_change_pct > 0.15:
                return True, f"atr_regime_shift_{atr_change_pct:.4f}"

        # Check 3: Position state changed (opened/closed since last analysis)
        current_has_position = self._get_current_position_data() is not None
        if current_has_position != self._last_analysis_had_position:
            return True, "position_state_changed"

        # v3 fix (NEW-BUG-1): No Check 4 for _needs_emergency_review — it is always False
        # here because it's consumed at on_timer() top (line 1911-1912).

        # Check 4: v18.3 Post-close forced analysis — give AI multiple re-entry chances.
        # Placed AFTER Checks 1-3 so natural triggers aren't wasted on the counter.
        # Counter set by on_position_closed() / _clear_position_state().
        if self._force_analysis_cycles_remaining > 0:
            self._force_analysis_cycles_remaining -= 1
            return True, f"post_close_reentry ({self._force_analysis_cycles_remaining} remaining)"

        return False, "no_significant_change"

    # v15.0 P2: Confidence level mapping for decay tracking
    _CONFIDENCE_LEVELS = {'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}

    def _check_confidence_decay(self, signal_data: dict) -> None:
        """
        v15.0 P2: Track confidence trajectory for open position.
        Implements graduated warning response to conviction decay.
        Warning-only — no automated SL/TP modification.
        """
        current_position = self._get_current_position_data()
        if not current_position:
            return

        current_confidence = signal_data.get('confidence', 'MEDIUM')
        current_signal = signal_data.get('signal', 'HOLD')

        # Update rolling window (keep last 4)
        self._confidence_history.append(current_confidence)
        if len(self._confidence_history) > 4:
            self._confidence_history = self._confidence_history[-4:]

        # Reuse existing entry confidence variable (DESIGN-P2-1)
        entry_confidence = self._position_entry_confidence
        if not entry_confidence:
            return

        entry_level = self._CONFIDENCE_LEVELS.get(entry_confidence, 2)
        current_level = self._CONFIDENCE_LEVELS.get(current_confidence, 2)

        # Level 1: Warning — confidence dropped below entry (per-level dedup via DESIGN-P2-2)
        if current_level < entry_level and current_level not in self._decay_warned_levels:
            self._decay_warned_levels.add(current_level)
            self.log.warning(
                f"⚠️ Confidence decay: {entry_confidence} → {current_confidence} "
                f"(history: {self._confidence_history})"
            )
            if self.telegram_bot and self.enable_telegram:
                try:
                    self.telegram_bot.send_message_sync(
                        f"⚠️ 信心衰减: {entry_confidence} → {current_confidence}\n"
                        f"历史: {' → '.join(self._confidence_history)}\n\n"
                        f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC",
                        broadcast=False,
                    )
                except Exception as e:
                    self.log.debug(f"Confidence decay notification failed (non-critical): {e}")

        # Level 2: Sustained decay — last 2 readings below entry confidence
        recent = self._confidence_history[-2:] if len(self._confidence_history) >= 2 else []
        if len(recent) == 2:
            recent_levels = [self._CONFIDENCE_LEVELS.get(c, 2) for c in recent]
            if all(level < entry_level for level in recent_levels):
                self.log.warning(
                    f"🔒 Confidence sustained decay: last 2 readings below "
                    f"entry ({entry_confidence}). Consider tightening SL."
                )

        # Level 3: Direction reversal — AI recommends opposite direction
        # Uses .upper() to normalize case (position side is lowercase from API)
        position_side = current_position.get('side', '').upper()
        if position_side and current_signal in ('LONG', 'SHORT') and current_signal != position_side:
            self.log.warning(
                f"🚨 Direction reversal detected: position={position_side}, "
                f"AI now recommends {current_signal}. Operator review recommended."
            )
            if self.telegram_bot and self.enable_telegram:
                try:
                    _pos_cn = self.telegram_bot.side_to_cn(position_side, 'position')
                    _sig_cn = self.telegram_bot.side_to_cn(current_signal, 'side') if current_signal in ('LONG', 'SHORT') else current_signal
                    self.telegram_bot.send_message_sync(
                        f"🚨 方向反转预警\n"
                        f"当前持仓: {_pos_cn}\n"
                        f"AI 建议: {_sig_cn}\n"
                        f"信心: {current_confidence}",
                        broadcast=False,
                    )
                except Exception as e:
                    self.log.debug(f"Telegram reversal alert (non-critical): {e}")

    def _check_stoploss_cooldown(self) -> bool:
        """
        v6.0: Check if we're in a per-stop cooldown period.

        Returns True if cooldown is active (should skip trading).
        Also refines stop type if we now have enough bar data.
        """

        if not self.cooldown_enabled or not self._stoploss_cooldown_until:
            return False

        now = datetime.now(timezone.utc)

        # Refine stop type after observation period (if still "default")
        if (self._stoploss_cooldown_type == "default"
                and self._last_stoploss_price
                and self._last_stoploss_time):

            elapsed_sec = (now - self._last_stoploss_time).total_seconds()
            detection_time = self.cooldown_detection_candles * self.config.timer_interval_sec

            if elapsed_sec >= detection_time:
                # We have enough data to classify the stop type
                self._refine_stop_type()

        if now < self._stoploss_cooldown_until:
            remaining = (self._stoploss_cooldown_until - now).total_seconds() / 60
            self.log.info(
                f"⏸️ Per-stop cooldown active ({self._stoploss_cooldown_type}): "
                f"{remaining:.0f} min remaining"
            )
            return True

        # Cooldown expired — clear state
        self.log.info("✅ Per-stop cooldown expired, trading resumed")
        self._stoploss_cooldown_until = None
        self._stoploss_cooldown_type = ""
        self._last_stoploss_price = None
        self._last_stoploss_time = None
        self._last_stoploss_side = None
        return False

    def _refine_stop_type(self):
        """
        v6.0: Refine stop type after observation period.

        Checks if price recovered (noise) or continued away (reversal).
        Adjusts cooldown duration accordingly.
        """

        if not self._last_stoploss_price or not self._last_stoploss_side:
            return

        # Get current price
        with self._state_lock:
            current_price = self._cached_current_price
        if not current_price or current_price <= 0:
            return

        sl_price = self._last_stoploss_price
        atr = self._cached_atr_value
        timer_interval = self.config.timer_interval_sec

        if atr <= 0:
            return

        # Determine if price recovered past SL level (noise stop)
        # or continued away from SL (reversal stop)
        if self._last_stoploss_side in ('LONG', 'BUY'):
            # Long was stopped out: SL was below entry
            # Noise: price recovered above SL level
            # Reversal: price continued below SL by >= 1 ATR
            price_from_sl = current_price - sl_price
            if price_from_sl > 0:
                new_type = "noise"
                candles = self.cooldown_noise_stop_candles
            elif abs(price_from_sl) >= atr:
                new_type = "reversal"
                candles = self.cooldown_reversal_stop_candles
            else:
                return  # Still indeterminate, keep default
        else:
            # Short was stopped out: SL was above entry
            price_from_sl = sl_price - current_price
            if price_from_sl > 0:
                new_type = "noise"
                candles = self.cooldown_noise_stop_candles
            elif abs(price_from_sl) >= atr:
                new_type = "reversal"
                candles = self.cooldown_reversal_stop_candles
            else:
                return

        # Update cooldown
        old_type = self._stoploss_cooldown_type
        self._stoploss_cooldown_type = new_type
        new_cooldown_end = self._last_stoploss_time + timedelta(seconds=candles * timer_interval)

        # Apply refined cooldown (noise → shorter, reversal → longer than default)
        now = datetime.now(timezone.utc)
        if new_cooldown_end > now:
            self._stoploss_cooldown_until = new_cooldown_end
            self.log.info(
                f"🔄 Stop type refined: {old_type} → {new_type} | "
                f"cooldown adjusted to {candles} candles"
            )
        else:
            # Refined cooldown already expired
            self._stoploss_cooldown_until = now
            self.log.info(
                f"🔄 Stop type refined: {old_type} → {new_type} | "
                f"cooldown already expired"
            )

    def _load_position_layers(self) -> List[Dict[str, Any]]:
        """Load pyramid layers from file (restart-safe persistence)."""
        try:
            if os.path.exists(self._position_layers_file):
                with open(self._position_layers_file, 'r') as f:
                    data = json.load(f)
                if isinstance(data, list) and data:
                    self.log.info(f"📊 Loaded {len(data)} pyramid layers from {self._position_layers_file}")
                    return data
        except Exception as e:
            self.log.warning(f"Failed to load position layers: {e}")
        return []

    def _save_position_layers(self):
        """Save pyramid layers to file (restart-safe persistence)."""
        try:
            os.makedirs(os.path.dirname(self._position_layers_file) or '.', exist_ok=True)
            with open(self._position_layers_file, 'w') as f:
                json.dump(self._position_layers, f, indent=2)
        except Exception as e:
            self.log.warning(f"Failed to save position layers: {e}")

    # ===== v7.2: Per-Layer SL/TP Helpers =====

    def _create_layer(
        self,
        entry_price: float,
        quantity: float,
        side: str,
        sl_price: float,
        tp_price: float,
        sl_order_id: str,
        tp_order_id: str,
        confidence: str = "MEDIUM",
        trailing_order_id: str = "",
        trailing_offset_bps: int = 0,
        trailing_activation_price: float = 0.0,
    ) -> str:
        """Create a new layer in _layer_orders. Returns the layer_id."""
        layer_idx = self._next_layer_idx
        self._next_layer_idx += 1
        layer_id = f"layer_{layer_idx}"

        self._layer_orders[layer_id] = {
            'entry_price': entry_price,
            'quantity': quantity,
            'side': side.lower(),
            'sl_order_id': sl_order_id,
            'tp_order_id': tp_order_id,
            'trailing_order_id': trailing_order_id,
            'sl_price': sl_price,
            'tp_price': tp_price,
            'trailing_offset_bps': trailing_offset_bps,
            'trailing_activation_price': trailing_activation_price,
            'highest_price': entry_price,
            'lowest_price': entry_price,
            'confidence': confidence,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'layer_index': layer_idx,
        }

        # Update reverse mapping
        if sl_order_id:
            self._order_to_layer[sl_order_id] = layer_id
        if tp_order_id:
            self._order_to_layer[tp_order_id] = layer_id
        if trailing_order_id:
            self._order_to_layer[trailing_order_id] = layer_id

        self._update_aggregate_sltp_state()
        self._persist_layer_orders()

        self.log.info(
            f"📊 Layer {layer_idx} created: {side} {quantity:.4f} BTC @ ${entry_price:,.2f} "
            f"(SL ${sl_price:,.2f}, TP ${tp_price:,.2f})"
        )
        return layer_id

    def _remove_layer(self, layer_id: str):
        """Remove a layer and clean up reverse mapping."""
        layer = self._layer_orders.pop(layer_id, None)
        if not layer:
            return

        # Save last layer snapshot before clearing (for on_position_closed evaluation)
        if not self._layer_orders:
            self._pre_close_last_layer = layer.copy()

        # Clean reverse mapping (SL + TP + trailing)
        for key in ('sl_order_id', 'tp_order_id', 'trailing_order_id'):
            order_id = layer.get(key, '')
            if order_id:
                self._order_to_layer.pop(order_id, None)

        self._update_aggregate_sltp_state()
        self._persist_layer_orders()
        self.log.info(f"📊 Layer {layer.get('layer_index', '?')} removed ({layer_id})")

    def _update_layer_order_id(self, layer_id: str, order_type: str, new_order_id: str):
        """Update a layer's SL, TP, or trailing order ID (e.g. after replace)."""
        layer = self._layer_orders.get(layer_id)
        if not layer:
            return

        if order_type == 'trailing':
            key = 'trailing_order_id'
        elif order_type == 'sl':
            key = 'sl_order_id'
        else:
            key = 'tp_order_id'
        old_id = layer.get(key, '')
        if old_id:
            self._order_to_layer.pop(old_id, None)

        layer[key] = new_order_id
        if new_order_id:
            self._order_to_layer[new_order_id] = layer_id

    def _reconcile_layer_quantities(self, current_position: Dict[str, Any]):
        """
        v15.6: Reconcile _layer_orders total quantity against Binance position.

        Detects drift from:
        - Partial liquidation (Binance engine reduces position)
        - External reduction via Binance APP / API
        - Any out-of-band position change

        If drift detected, proportionally scales all layer quantities to match
        the actual Binance position. This ensures emergency SL submissions use
        the correct quantity and AI decisions see accurate layer structure.

        Following NautilusTrader's reconciliation pattern: the framework handles
        order/position reconciliation with the venue, but strategy-level tracking
        (_layer_orders) requires its own reconciliation logic.
        """
        if not self._layer_orders:
            return

        binance_qty = abs(float(current_position.get('quantity', 0)))
        if binance_qty <= 0:
            return

        layer_total = sum(
            l.get('quantity', 0) for l in self._layer_orders.values()
        )
        if layer_total <= 0:
            return

        # Tolerance: 0.1% or 0.0001 BTC (whichever is larger) to avoid
        # false positives from floating-point rounding differences
        tolerance = max(binance_qty * 0.001, 0.0001)
        drift = layer_total - binance_qty

        if abs(drift) <= tolerance:
            return

        # Drift detected — proportionally scale all layers
        scale_factor = binance_qty / layer_total
        self.log.warning(
            f"⚠️ Layer quantity drift: layers={layer_total:.4f} BTC, "
            f"Binance={binance_qty:.4f} BTC, drift={drift:+.4f} BTC. "
            f"Scaling layers ×{scale_factor:.4f}"
        )

        # v24.0: Track layers needing order resubmission
        layers_to_resubmit = []

        for layer_id, layer in self._layer_orders.items():
            old_qty = layer.get('quantity', 0)
            _step = float(self.instrument.size_increment)
            new_qty = math.floor(old_qty * scale_factor / _step) * _step
            if new_qty != old_qty:
                layer['quantity'] = new_qty
                layers_to_resubmit.append((layer_id, layer, new_qty))
                self.log.info(
                    f"  Layer {layer.get('layer_index', '?')}: "
                    f"{old_qty:.4f} → {new_qty:.4f} BTC"
                )

        self._persist_layer_orders()
        self._update_aggregate_sltp_state()

        # v24.0: Cancel and resubmit SL/TP orders with corrected quantities.
        # Without this, Binance orders retain old quantities → -2022 ReduceOnly
        # errors when the position has been externally reduced (partial liquidation).
        pos_side = current_position.get('side', 'unknown').lower()
        for layer_id, layer, new_qty in layers_to_resubmit:
            self._resubmit_layer_orders_with_quantity(layer_id, layer, new_qty, pos_side)

        # Telegram alert for visibility
        if self.telegram_bot and self.enable_telegram:
            try:
                pos_side = current_position.get('side', 'unknown')
                pos_cn = self.telegram_bot.side_to_cn(pos_side, 'position')
                self.telegram_bot.send_message_sync(
                    f"⚠️ *层级数量校正*\n\n"
                    f"仓位: {binance_qty:.4f} BTC {pos_cn}\n"
                    f"层级追踪: {layer_total:.4f} → {binance_qty:.4f} BTC\n"
                    f"漂移: {drift:+.4f} BTC\n"
                    f"原因: 可能为部分强平或外部减仓\n\n"
                    f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
                )
            except Exception as e:
                self.log.debug(f"Layer reconciliation Telegram alert (non-critical): {e}")

    def _resubmit_layer_orders_with_quantity(
        self,
        layer_id: str,
        layer: dict,
        new_qty: float,
        position_side: str,
    ):
        """
        v24.0: Cancel and resubmit a layer's SL/TP orders with corrected quantity.

        Called after _reconcile_layer_quantities detects drift from external
        position reduction (partial liquidation, Binance APP, etc.).

        Safety: submits new order FIRST → cancels old order (no protection gap).
        For trailing stops: resubmits as TRAILING_STOP_MARKET preserving offset.
        For fixed SL: resubmits as STOP_MARKET at same price.
        For TP: resubmits as LIMIT_IF_TOUCHED at same price.
        """
        exit_side = OrderSide.SELL if position_side == 'long' else OrderSide.BUY

        # --- Resubmit fixed SL (STOP_MARKET) ---
        # v24.2-fix: Fixed SL and trailing are independent orders (both can coexist).
        # Fixed SL is always resubmitted here; trailing is handled separately below.
        old_sl_id = layer.get('sl_order_id', '')
        if old_sl_id:
            new_sl_id = None
            sl_price = layer.get('sl_price', 0)
            if sl_price > 0:
                try:
                    sl_order = self.order_factory.stop_market(
                        instrument_id=self.instrument_id,
                        order_side=exit_side,
                        quantity=self.instrument.make_qty(new_qty),
                        trigger_price=self.instrument.make_price(sl_price),
                        trigger_type=TriggerType.LAST_PRICE,
                        reduce_only=True,
                    )
                    self.submit_order(sl_order)
                    new_sl_id = str(sl_order.client_order_id)
                except Exception as e:
                    self.log.error(
                        f"❌ Reconcile: failed to resubmit SL for {layer_id}: {e}"
                    )

            if new_sl_id:
                # New SL submitted → cancel old
                try:
                    old_order = self.cache.order(ClientOrderId(old_sl_id))
                    if old_order and old_order.is_open:
                        self._intentionally_cancelled_order_ids.add(old_sl_id)
                        self.cancel_order(old_order)
                except Exception as e:
                    self.log.warning(f"⚠️ Reconcile: failed to cancel old SL {old_sl_id[:8]}...: {e}")

                # Update tracking
                self._order_to_layer.pop(old_sl_id, None)
                self._order_to_layer[new_sl_id] = layer_id
                layer['sl_order_id'] = new_sl_id
                self.log.info(
                    f"🔄 Reconcile {layer_id} SL: {old_sl_id[:8]}... → {new_sl_id[:8]}... "
                    f"(qty={new_qty:.4f})"
                )
            else:
                self.log.error(
                    f"❌ Reconcile: could not resubmit SL for {layer_id}, "
                    f"old order {old_sl_id[:8]}... remains with wrong qty"
                )

        # --- Resubmit TP ---
        old_tp_id = layer.get('tp_order_id', '')
        tp_price = layer.get('tp_price', 0)
        if old_tp_id and tp_price > 0:
            new_tp_id = None
            try:
                tp_order = self.order_factory.limit_if_touched(
                    instrument_id=self.instrument_id,
                    order_side=exit_side,
                    quantity=self.instrument.make_qty(new_qty),
                    price=self.instrument.make_price(tp_price),
                    trigger_price=self.instrument.make_price(tp_price),
                    trigger_type=TriggerType.LAST_PRICE,
                    reduce_only=True,
                )
                self.submit_order(tp_order)
                new_tp_id = str(tp_order.client_order_id)
            except Exception as e:
                self.log.error(
                    f"❌ Reconcile: failed to resubmit TP for {layer_id}: {e}"
                )

            if new_tp_id:
                try:
                    old_order = self.cache.order(ClientOrderId(old_tp_id))
                    if old_order and old_order.is_open:
                        self._intentionally_cancelled_order_ids.add(old_tp_id)
                        self.cancel_order(old_order)
                except Exception as e:
                    self.log.warning(f"⚠️ Reconcile: failed to cancel old TP {old_tp_id[:8]}...: {e}")

                self._order_to_layer.pop(old_tp_id, None)
                self._order_to_layer[new_tp_id] = layer_id
                layer['tp_order_id'] = new_tp_id
                self.log.info(
                    f"🔄 Reconcile {layer_id} TP: {old_tp_id[:8]}... → {new_tp_id[:8]}... "
                    f"(qty={new_qty:.4f})"
                )
            else:
                self.log.error(
                    f"❌ Reconcile: could not resubmit TP for {layer_id}, "
                    f"old order {old_tp_id[:8]}... remains with wrong qty"
                )

        # --- Resubmit Trailing (independent from fixed SL, v24.0) ---
        old_trailing_id = layer.get('trailing_order_id', '')
        if old_trailing_id:
            trailing_bps = layer.get('trailing_offset_bps', 100)
            act_price = layer.get('trailing_activation_price', 0)
            new_trailing_id = None

            # Use current price as activation (trailing already submitted,
            # Binance will re-evaluate activation from new price)
            with self._state_lock:
                current_price = self._cached_current_price or act_price
            if current_price > 0:
                new_trailing_id = self._submit_trailing_stop(
                    layer_id=layer_id,
                    quantity=new_qty,
                    side=position_side,
                    activation_price=current_price,
                    trailing_offset_bps=trailing_bps,
                )

            if new_trailing_id:
                try:
                    old_order = self.cache.order(ClientOrderId(old_trailing_id))
                    if old_order and old_order.is_open:
                        self._intentionally_cancelled_order_ids.add(old_trailing_id)
                        self.cancel_order(old_order)
                except Exception as e:
                    self.log.warning(
                        f"⚠️ Reconcile: failed to cancel old trailing {old_trailing_id[:8]}...: {e}"
                    )

                self._order_to_layer.pop(old_trailing_id, None)
                self._order_to_layer[new_trailing_id] = layer_id
                layer['trailing_order_id'] = new_trailing_id
                self.log.info(
                    f"🔄 Reconcile {layer_id} Trailing: {old_trailing_id[:8]}... → "
                    f"{new_trailing_id[:8]}... (qty={new_qty:.4f})"
                )
            else:
                self.log.warning(
                    f"⚠️ Reconcile: could not resubmit trailing for {layer_id}, "
                    f"fixed SL still active as protection"
                )

        self._persist_layer_orders()

    def _update_aggregate_sltp_state(self):
        """Recompute aggregate sltp_state from all active layers."""
        instrument_key = str(self.instrument_id)

        if not self._layer_orders:
            # No layers → save snapshot before clearing.
            # on_order_filled fires BEFORE on_position_closed, so by the time
            # on_position_closed runs, sltp_state is already empty.
            # This snapshot preserves the last-known SL/TP for:
            #   - Telegram close reason detection (SL/TP vs manual)
            #   - evaluate_trade() planned SL/TP
            #   - SL cooldown activation
            if instrument_key in self.sltp_state:
                self._pre_close_sltp_snapshot = self.sltp_state[instrument_key].copy()
                self.sltp_state.pop(instrument_key, None)
            return

        # Compute aggregates from all layers
        total_qty = 0.0
        weighted_entry = 0.0
        tightest_sl = None
        nearest_tp = None
        side = None
        highest = 0.0
        lowest = float('inf')

        for layer in self._layer_orders.values():
            qty = layer['quantity']
            total_qty += qty
            weighted_entry += layer['entry_price'] * qty
            side = layer['side']

            # Track highest/lowest across all layers
            highest = max(highest, layer.get('highest_price', 0))
            lowest = min(lowest, layer.get('lowest_price', float('inf')))

            sl = layer.get('sl_price', 0)
            tp = layer.get('tp_price', 0)

            # Tightest SL = closest to triggering
            if sl > 0:
                if side == 'long':
                    tightest_sl = max(tightest_sl or 0, sl)  # Highest SL for long
                else:
                    tightest_sl = min(tightest_sl or float('inf'), sl)  # Lowest SL for short

            # Nearest TP = closest to triggering
            if tp > 0:
                if side == 'long':
                    nearest_tp = min(nearest_tp or float('inf'), tp)  # Lowest TP for long
                else:
                    nearest_tp = max(nearest_tp or 0, tp)  # Highest TP for short

        avg_entry = weighted_entry / total_qty if total_qty > 0 else 0

        # Preserve validated (original) SL/TP from _submit_bracket_order —
        # these are the planned values before emergency modifications.
        # evaluate_trade() uses them for accurate planned R/R calculation.
        existing = self.sltp_state.get(instrument_key, {})
        self.sltp_state[instrument_key] = {
            'entry_price': avg_entry,
            'highest_price': highest if highest > 0 else avg_entry,
            'lowest_price': lowest if lowest < float('inf') else avg_entry,
            'current_sl_price': tightest_sl or 0,
            'current_tp_price': nearest_tp or 0,
            'side': side.upper() if side else '',
            'quantity': total_qty,
            'validated_sl_price': existing.get('validated_sl_price'),
            'validated_tp_price': existing.get('validated_tp_price'),
        }

    def _persist_layer_orders(self):
        """Save layer orders to file for restart recovery."""
        try:
            filepath = "data/layer_orders.json"
            os.makedirs(os.path.dirname(filepath) or '.', exist_ok=True)
            with open(filepath, 'w') as f:
                json.dump(self._layer_orders, f, indent=2)
        except Exception as e:
            self.log.warning(f"Failed to persist layer orders: {e}")

    def _load_layer_orders(self) -> Dict[str, Dict[str, Any]]:
        """Load layer orders from file on startup."""
        try:
            filepath = "data/layer_orders.json"
            if os.path.exists(filepath):
                with open(filepath, 'r') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            self.log.warning(f"Failed to load layer orders: {e}")
        return {}

    def _reconstruct_layers_runtime(self, current_position: Dict[str, Any]):
        """
        v24.2: Runtime reconstruction of _layer_orders from exchange orders.

        Called when a position exists but _layer_orders is empty (e.g., after
        ghost detection misfire or unexpected state clearing). Reuses the same
        Tier 3 logic as _recover_sltp_on_start but can run during normal operation.
        """
        try:
            side = current_position.get('side', 'long')
            entry_px = float(current_position.get('entry_price',
                             current_position.get('avg_px', 0)))
            quantity = abs(float(current_position.get('quantity', 0)))

            open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
            reduce_only_orders = [o for o in open_orders if o.is_reduce_only]

            sl_orders = [
                o for o in reduce_only_orders
                if o.order_type in (OrderType.STOP_MARKET, OrderType.TRAILING_STOP_MARKET)
            ]
            tp_orders = [
                o for o in reduce_only_orders
                if o.order_type not in (OrderType.STOP_MARKET, OrderType.TRAILING_STOP_MARKET)
            ]

            if not sl_orders:
                self.log.warning(
                    "⚠️ Runtime reconstruction: no SL orders found on exchange. "
                    "Submitting emergency SL."
                )
                self._submit_emergency_sl(
                    quantity=quantity,
                    position_side=side,
                    reason="运行时重建: _layer_orders 为空且无交易所 SL",
                )
                return

            # Rebuild layers from SL orders (same logic as Tier 3 in _recover_sltp_on_start)
            self._layer_orders.clear()
            self._order_to_layer.clear()
            tp_pool = list(tp_orders)

            for i, sl_o in enumerate(sl_orders):
                sl_qty = float(sl_o.quantity)
                sl_price = 0.0
                if hasattr(sl_o, 'trigger_price') and sl_o.trigger_price is not None:
                    sl_price = float(sl_o.trigger_price)
                sl_id = str(sl_o.client_order_id)
                is_trailing = sl_o.order_type == OrderType.TRAILING_STOP_MARKET

                # Try to find matching TP by quantity
                tp_id = ""
                tp_price = 0.0
                for tp_o in tp_pool:
                    tp_qty = float(tp_o.quantity)
                    if abs(tp_qty - sl_qty) / max(sl_qty, 0.001) < 0.05:
                        tp_price = float(tp_o.price) if hasattr(tp_o, 'price') and tp_o.price else 0.0
                        tp_id = str(tp_o.client_order_id)
                        tp_pool.remove(tp_o)
                        break

                layer_id = f"layer_{i}"
                # v24.2-fix: If this is a trailing order, it's the trailing protection,
                # not a fixed SL. Avoid sl_order_id = trailing_order_id collision.
                trailing_bps = 0
                trailing_act_price = 0.0
                if is_trailing:
                    if hasattr(sl_o, 'trailing_offset') and sl_o.trailing_offset:
                        trailing_bps = int(float(sl_o.trailing_offset))
                    if hasattr(sl_o, 'activation_price') and sl_o.activation_price:
                        trailing_act_price = float(sl_o.activation_price)
                self._layer_orders[layer_id] = {
                    'entry_price': entry_px,
                    'quantity': sl_qty,
                    'side': side.lower(),
                    'sl_order_id': '' if is_trailing else sl_id,
                    'tp_order_id': tp_id,
                    'trailing_order_id': sl_id if is_trailing else '',
                    'sl_price': sl_price,
                    'tp_price': tp_price,
                    'trailing_offset_bps': trailing_bps,
                    'trailing_activation_price': trailing_act_price,
                    'highest_price': entry_px,
                    'lowest_price': entry_px,
                    'confidence': 'MEDIUM',
                    'timestamp': '',
                    'layer_index': i,
                }
                if sl_id:
                    self._order_to_layer[sl_id] = layer_id
                if tp_id:
                    self._order_to_layer[tp_id] = layer_id

            self._next_layer_idx = len(self._layer_orders)
            self._update_aggregate_sltp_state()
            self._persist_layer_orders()
            self.log.info(
                f"✅ Runtime reconstructed {len(self._layer_orders)} layers "
                f"from {len(sl_orders)} SL + {len(tp_orders)} TP orders"
            )
        except Exception as e:
            self.log.error(f"❌ Runtime layer reconstruction failed: {e}")

    def _get_layers_sorted(self, order: str = 'lifo') -> list:
        """Get layers sorted by entry time. 'lifo'=newest first, 'fifo'=oldest first."""
        items = list(self._layer_orders.items())
        items.sort(key=lambda x: x[1].get('layer_index', 0), reverse=(order == 'lifo'))
        return items

    # ========================== v24.0 Trailing Stop Activation ==========================

    # Minimum unrealized profit in R-multiples to activate trailing.
    # v43.0: Raised to 1.5R (from 1.1R) to match 4H ATR trailing distance.
    # With 4H ATR × 0.6 callback, activation must be high enough that
    # worst-case trailing exit (activation - callback) stays profitable.
    # 1.5R provides ample buffer for 4H-scale callback distance.
    _TRAILING_ACTIVATION_R = 1.5

    # ========================== Pyramiding Methods ==========================

    def _check_pyramiding_allowed(
        self,
        current_position: Dict[str, Any],
        confidence: str,
        signal_data: Dict[str, Any],
    ) -> tuple:
        """
        v6.0: Check if adding to position (pyramiding) is allowed.

        Returns (allowed: bool, reason: str, layer_size_ratio: float)
        """
        if not self.pyramiding_enabled:
            return False, "金字塔加仓已禁用", 0.0

        current_layers = len(self._position_layers)

        # Rule 1: Confidence must meet minimum
        confidence_levels = {'LOW': 0, 'MEDIUM': 1, 'HIGH': 2}
        min_level = confidence_levels.get(self.pyramiding_min_confidence, 2)
        signal_level = confidence_levels.get(confidence, 1)
        if signal_level < min_level:
            return False, f"加仓信心不足 ({confidence} < {self.pyramiding_min_confidence})", 0.0

        # Rule 3: Counter-trend check
        if not self.pyramiding_counter_trend_allowed:
            # Mechanical mode: trend_context already evaluated by anticipatory scoring
            _trend_ctx = signal_data.get('_trend_context', '') if signal_data else ''
            if _trend_ctx in ('CONFIRMING', 'NEUTRAL'):
                pass  # Mechanical system confirms trend alignment — skip 30M DI check
            else:
                technical_data = self.latest_technical_data or {}
                is_long = current_position.get('side', '') == 'long'
                if _is_counter_trend(is_long, technical_data):
                    return False, "逆势仓位禁止加仓", 0.0

        # Rule 4: Must be profitable by >= min_profit_atr ATR
        entry_price = current_position.get('entry_price', 0)
        with self._state_lock:
            current_price = self._cached_current_price
        atr = self._cached_atr_value

        if entry_price > 0 and current_price > 0 and atr > 0:
            if current_position.get('side') == 'long':
                unrealized_distance = current_price - entry_price
            else:
                unrealized_distance = entry_price - current_price

            min_distance = atr * self.pyramiding_min_profit_atr
            if unrealized_distance < min_distance:
                return (
                    False,
                    f"浮盈不足 ({unrealized_distance:.0f} < {min_distance:.0f} = {self.pyramiding_min_profit_atr}×ATR)",
                    0.0,
                )

        # Rule 5: ADX check
        technical_data = self.latest_technical_data or {}
        adx = technical_data.get('adx', 0) or 0
        if adx < self.pyramiding_min_adx:
            return False, f"趋势不明确 (ADX {adx:.0f} < {self.pyramiding_min_adx:.0f})", 0.0

        # Rule 6: Funding rate check (v6.7: direction-aware, use actual data source)
        # FR > 0 → Long pays, Short receives; FR < 0 → Short pays, Long receives
        # Only block when the position side is PAYING funding at high rates
        fr_data = (self.latest_derivatives_data or {}).get('funding_rate', {})
        fr_pct = fr_data.get('current_pct', fr_data.get('settled_pct', 0)) or 0
        try:
            fr_pct = float(fr_pct)
        except (ValueError, TypeError) as e:
            self.log.debug(f"Using default value, original error: {e}")
            fr_pct = 0
        fr_abs = abs(fr_pct / 100) if abs(fr_pct) > 0.1 else abs(fr_pct)  # Handle % vs decimal
        is_long = current_position.get('side', '') == 'long'
        paying_funding = (is_long and fr_pct > 0) or (not is_long and fr_pct < 0)
        if paying_funding and fr_abs > self.pyramiding_max_funding_rate:
            return (
                False,
                f"Funding Rate 成本过高 ({fr_pct:.5f}%, {('Long支付' if is_long else 'Short支付')}, "
                f"|FR|={fr_abs:.5f} > {self.pyramiding_max_funding_rate:.5f})",
                0.0,
            )

        # All checks passed - determine layer size
        # current_layers starts at 1 (initial position), so first add uses layer_sizes[1]
        next_layer_idx = current_layers
        if next_layer_idx < len(self.pyramiding_layer_sizes):
            layer_ratio = self.pyramiding_layer_sizes[next_layer_idx]
        else:
            layer_ratio = self.pyramiding_layer_sizes[-1]  # Use last size for any extra layers

        return True, "", layer_ratio

    def _close_position_only(self, current_position: Dict[str, Any]):
        """
        v3.12: Close position without opening opposite side.

        This is used when AI sends CLOSE signal, meaning:
        - Close the current position completely
        - Do NOT open any new position
        - Cancel all pending SL/TP orders first

        Different from reversal which closes then opens opposite.
        """
        # v4.4: Re-verify position exists before submitting reduce_only order
        # Position might have been closed by SL/TP between signal analysis and execution
        verified_position = self._get_current_position_data()
        if not verified_position:
            self.log.warning("⚠️ Position no longer exists (likely closed by SL/TP), skipping close order")
            self._last_signal_status = {
                'executed': False,
                'reason': '仓位已平仓 (SL/TP 触发)',
                'action_taken': '',
            }
            return

        current_side = verified_position['side']
        current_qty = verified_position['quantity']
        side_cn = '多' if current_side == 'long' else '空'

        self.log.info(f"🔴 Closing {current_side} position: {current_qty:.4f} BTC (CLOSE signal)")

        # Cancel all pending orders (SL/TP) before closing
        try:
            open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
            if open_orders:
                self.log.info(f"🗑️ Cancelling {len(open_orders)} pending orders before close")
                self.cancel_all_orders(self.instrument_id)
        except Exception as e:
            self.log.warning(f"⚠️ Failed to cancel some orders: {e}, continuing with close")

        # Submit close order (reduce_only=True)
        close_side = OrderSide.SELL if current_side == 'long' else OrderSide.BUY
        self._submit_order(
            side=close_side,
            quantity=current_qty,
            reduce_only=True,
        )

        # Update signal status
        self._last_signal_status = {
            'executed': True,
            'reason': '',
            'action_taken': f'平{side_cn}仓 {current_qty:.4f} BTC',
        }

        # v4.2: Telegram notification moved to on_position_closed to avoid duplicate messages
        # The on_position_closed event will send a single comprehensive close notification

    def _reduce_position(self, current_position: Dict[str, Any], target_pct: int):
        """
        v7.2: Reduce position using LIFO layer removal.

        Closes newest layers first until target reduction is met.
        Each closed layer's SL/TP orders are cancelled (its peer auto-cancels).

        Args:
            current_position: Current position info dict
            target_pct: Target position size as percentage (0-100)
                       0 = close all, 50 = keep half, 100 = no change
        """
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

        current_side = verified_position['side']
        current_qty = verified_position['quantity']
        side_cn = '多' if current_side == 'long' else '空'

        # Validate and calculate target quantity
        target_pct = max(0, min(100, target_pct))  # Clamp to 0-100

        if target_pct >= 100:
            self.log.info(f"📊 REDUCE signal with 100% - no action needed")
            self._last_signal_status = {
                'executed': False,
                'reason': '减仓比例=100%',
                'action_taken': '维持现有仓位',
            }
            return

        if target_pct == 0:
            self.log.info(f"📊 REDUCE signal with 0% - equivalent to CLOSE")
            self._close_position_only(current_position)
            return

        # Calculate reduction amount
        target_qty = current_qty * (target_pct / 100.0)
        reduce_qty = current_qty - target_qty

        # Check minimum trade amount
        if reduce_qty < self.position_config['min_trade_amount']:
            self.log.warning(
                f"⚠️ Reduce quantity {reduce_qty:.4f} below minimum "
                f"{self.position_config['min_trade_amount']:.4f}, skipping"
            )
            self._last_signal_status = {
                'executed': False,
                'reason': f'减仓量低于最小交易量',
                'action_taken': '',
            }
            return

        # v4.0 (A3): Mutual exclusion check
        if self._pending_sltp:
            self.log.warning("⚠️ Cannot reduce while _pending_sltp is active, skipping")
            self._last_signal_status = {
                'executed': False,
                'reason': '等待入场SL/TP提交中',
                'action_taken': '',
            }
            return

        self.log.info(
            f"📉 Reducing {current_side} position by {100-target_pct}%: "
            f"{current_qty:.4f} → {target_qty:.4f} BTC"
        )

        # v7.2: LIFO layer removal — cancel newest layers' SL/TP, then market close
        remaining_to_reduce = reduce_qty
        layers_to_remove = []

        for layer_id, layer in self._get_layers_sorted(order='lifo'):
            if remaining_to_reduce <= 0:
                break
            layer_qty = layer['quantity']
            close_qty = min(layer_qty, remaining_to_reduce)

            # Cancel this layer's SL/TP/trailing orders
            for key in ('sl_order_id', 'tp_order_id', 'trailing_order_id'):
                order_id = layer.get(key, '')
                if order_id:
                    try:
                        order = self.cache.order(ClientOrderId(order_id))
                        if order and order.is_open:
                            self._intentionally_cancelled_order_ids.add(order_id)
                            self.cancel_order(order)
                    except Exception as e:
                        self.log.warning(f"⚠️ Failed to cancel {key} for {layer_id}: {e}")

            layers_to_remove.append((layer_id, close_qty, layer_qty))
            remaining_to_reduce -= close_qty

        # Submit market reduce order for total reduction
        reduce_side = OrderSide.SELL if current_side == 'long' else OrderSide.BUY
        actual_reduce = reduce_qty - max(0, remaining_to_reduce)
        if actual_reduce > 0:
            self._submit_order(
                side=reduce_side,
                quantity=actual_reduce,
                reduce_only=True,
            )

        # Remove fully-closed layers, update partially-closed layers
        for layer_id, close_qty, layer_qty in layers_to_remove:
            if close_qty >= layer_qty:
                self._remove_layer(layer_id)
            else:
                # Partial layer reduction: reduce layer quantity + resubmit SL/TP
                if layer_id in self._layer_orders:
                    new_layer_qty = layer_qty - close_qty
                    # Resubmit SL/TP with corrected quantity to match reduced layer.
                    # Only update layer quantity AFTER successful resubmit to avoid
                    # stale state where quantity is reduced but SL/TP still reference
                    # old quantity (or worse, are gone after cancel+failed resubmit).
                    position_side = 'long' if current_side == 'long' else 'short'
                    try:
                        self._layer_orders[layer_id]['quantity'] = new_layer_qty
                        self._resubmit_layer_orders_with_quantity(
                            layer_id, self._layer_orders[layer_id],
                            new_layer_qty, position_side,
                        )
                    except Exception as e:
                        # Resubmit failed — restore old quantity so layer tracking
                        # stays consistent with whatever protection orders remain.
                        self._layer_orders[layer_id]['quantity'] = layer_qty
                        self.log.error(
                            f"⚠️ Failed to resubmit SL/TP for reduced layer {layer_id}: {e}. "
                            f"Quantity restored to {layer_qty:.4f}. "
                            f"Emergency SL will re-protect on next on_timer."
                        )
                        # Submit emergency SL to cover protection gap
                        try:
                            self._submit_emergency_sl(
                                quantity=new_layer_qty,
                                position_side=position_side,
                                reason=f'SL/TP resubmit failed after partial reduce of layer {layer_id}',
                            )
                        except Exception as em_e:
                            self.log.error(f"❌ Emergency SL after failed resubmit also failed: {em_e}")
                    self._update_aggregate_sltp_state()
                    self._persist_layer_orders()

        # Update _position_layers for memory tracking
        if self._position_layers:
            keep_ratio = target_pct / 100.0
            for layer in self._position_layers:
                layer['quantity'] = layer.get('quantity', 0) * keep_ratio
            self._save_position_layers()

        # Update signal status
        self._last_signal_status = {
            'executed': True,
            'reason': '',
            'action_taken': f'减{side_cn}仓 {100-target_pct}% (-{actual_reduce:.4f} BTC)',
        }

        # v4.2: Telegram notification moved to on_position_changed events
        # This avoids duplicate messages (order submission + position update)

    def _submit_order(
        self,
        side: OrderSide,
        quantity: float,
        reduce_only: bool = False,
    ):
        """Submit market order to exchange."""
        if quantity < self.position_config['min_trade_amount']:
            self.log.warning(
                f"⚠️ Order quantity {quantity:.3f} below minimum "
                f"{self.position_config['min_trade_amount']:.3f}, skipping"
            )
            return

        # v3.17: Final verification for reduce_only orders to prevent -2022 error
        # Race condition: Position might be closed by SL/TP between verification and submission
        if reduce_only:
            current_position = self._get_current_position_data()
            if not current_position:
                self.log.warning(
                    f"⚠️ Skipping reduce_only order - no position exists "
                    f"(likely closed by SL/TP between verification and submission)"
                )
                return
            # Verify quantity doesn't exceed position size
            position_qty = current_position.get('quantity', 0)
            if quantity > position_qty:
                self.log.warning(
                    f"⚠️ Adjusting reduce_only quantity from {quantity:.3f} to {position_qty:.3f} "
                    f"(can't reduce more than position size)"
                )
                quantity = position_qty
                if quantity <= 0:
                    self.log.warning("⚠️ Position quantity is 0, skipping reduce_only order")
                    return

        # v4.9: Validate minimum notional for non-reduce orders
        # Use dynamic exchange filter (fetched at startup), fallback to config
        if not reduce_only:
            _min_notional_val = float(self.instrument.min_notional) if self.instrument.min_notional else get_min_notional_usdt()
            safety_margin = get_min_notional_safety_margin()

            # Get current price for notional calculation
            current_price = getattr(self, '_cached_current_price', None)
            if not current_price and self.indicator_manager.recent_bars:
                current_price = float(self.indicator_manager.recent_bars[-1].close)

            if current_price:
                notional = quantity * current_price
                min_required = _min_notional_val * safety_margin

                if notional < min_required:
                    # Calculate minimum quantity needed, round up to exchange step_size
                    _step = float(self.instrument.size_increment)
                    _step_inv = round(1.0 / _step)
                    min_qty = math.ceil(min_required / current_price * _step_inv) / _step_inv

                    self.log.warning(
                        f"⚠️ Order notional ${notional:.2f} below minimum ${_min_notional_val:.0f}, "
                        f"adjusting quantity from {quantity:.3f} to {min_qty:.3f} BTC"
                    )
                    quantity = min_qty

        # Create market order
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=side,
            quantity=self.instrument.make_qty(quantity),
            time_in_force=TimeInForce.GTC,
            reduce_only=reduce_only,
        )

        # Submit order
        self.submit_order(order)

        self.log.info(
            f"📤 Submitted {side.name} market order: {quantity:.3f} BTC "
            f"(reduce_only={reduce_only})"
        )

    def _process_pending_reflections(self):
        """
        v12.0/v46.0: Clear pending reflections queue.

        Mechanical mode has no LLM reflection — grade-based template lesson
        from record_outcome() is kept as-is. Queue is cleared to discard
        entries appended by event_handlers.on_position_closed().
        """
        self._pending_reflections.clear()

    def _cancel_pending_entry_order(self):
        """
        v4.17: Cancel unfilled LIMIT entry order from previous cycle.

        Called at the start of on_timer to clean up stale entry orders before
        new analysis. If the LIMIT entry didn't fill since last cycle, the market
        has moved away — cancel and let new analysis decide fresh.
        """
        pending_id = self._pending_entry_order_id
        if not pending_id:
            return

        try:
            open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
            for order in open_orders:
                if str(order.client_order_id) == pending_id:
                    self.cancel_order(order)
                    self.log.info(
                        f"🗑️ Cancelled unfilled LIMIT entry order: {pending_id[:8]}... "
                        f"(market moved away, will re-analyze)"
                    )
                    # v19.0: Clear signal fingerprint — allow same signal to re-submit.
                    # Without this, LIMIT cancel → same signal next cycle → "duplicate signal" deadlock.
                    if self._last_executed_fingerprint:
                        self.log.info(
                            f"🔓 Cleared fingerprint '{self._last_executed_fingerprint}' "
                            f"(LIMIT unfilled, allow re-entry)"
                        )
                        self._last_executed_fingerprint = ""
                    # Clear pending SL/TP state since entry won't fill
                    self._pending_sltp = None
                    # Clear pre-initialized sltp_state
                    instrument_key = str(self.instrument_id)
                    if instrument_key in self.sltp_state:
                        state = self.sltp_state[instrument_key]
                        # Only clear if this was from the pending entry (no SL order yet)
                        if state.get("sl_order_id") is None:
                            del self.sltp_state[instrument_key]

                    if self.telegram_bot and self.enable_telegram:
                        try:
                            self.telegram_bot.send_message_sync(
                                "ℹ️ 入场限价单未成交，已取消\n"
                                "价格已偏离，等待下次分析重新评估\n\n"
                                f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
                            )
                        except Exception as e:
                            self.log.debug(f"Telegram notification failure is non-critical: {e}")
                            pass  # Telegram notification failure is non-critical
                    break
            else:
                # Order not in open orders — already filled or rejected
                self.log.debug(
                    f"Pending entry {pending_id[:8]}... no longer open (filled or already cancelled)"
                )
        except Exception as e:
            self.log.warning(f"⚠️ Error cancelling pending entry order: {e}")
        finally:
            self._pending_entry_order_id = None

    def _submit_bracket_order(
        self,
        side: OrderSide,
        quantity: float,
    ):
        """
        Submit entry order with pending SL/TP (two-phase approach).

        v4.13: NautilusTrader 1.222.0's _submit_order_list() rejects bracket orders
        with linked_order_ids (UNSUPPORTED_OCO_CONDITIONAL_ORDERS) because Binance
        migrated conditional orders (STOP_MARKET, TAKE_PROFIT_MARKET) to the Algo
        Order API (/fapi/v1/algoOrder) in Dec 2025. The old bracket approach
        (order_factory.bracket → submit_order_list) is no longer viable.

        v6.6: Both SL and TP now go through Binance Algo API (position-linked):
        - SL: stop_market() → STOP_MARKET → /fapi/v1/algoOrder
        - TP: limit_if_touched() → TAKE_PROFIT → /fapi/v1/algoOrder
        Binance auto-cancels both when the position closes.

        Approach:
        1. Submit LIMIT entry order at validated entry_price (v4.17: was MARKET)
        2. Store pending SL/TP prices in self._pending_sltp
        3. on_position_opened() submits SL + TP as individual orders
        4. OCO managed manually in on_order_filled() (already implemented)
        5. Unfilled LIMIT entries cancelled by on_timer next cycle (v4.17)

        Parameters
        ----------
        side : OrderSide
            Side of the entry order (BUY or SELL)
        quantity : float
            Quantity to trade
        """
        if quantity < self.position_config['min_trade_amount']:
            self.log.warning(
                f"⚠️ Order quantity {quantity:.3f} below minimum "
                f"{self.position_config['min_trade_amount']:.3f}, skipping"
            )
            return

        if not self.enable_auto_sl_tp:
            self.log.warning("⚠️ Auto SL/TP is disabled - submitting market order with emergency SL")
            self._submit_order(side=side, quantity=quantity, reduce_only=False)
            # v6.0: Submit emergency SL even when auto SL/TP is disabled
            # Without this, position is completely unprotected until next orphan detection (~20min)
            position_side = "long" if side == OrderSide.BUY else "short"
            self._submit_emergency_sl(
                quantity=quantity,
                position_side=position_side,
                reason="Auto SL/TP disabled — emergency SL as safety net"
            )
            return

        # v4.11: Use shared SL/TP validation (unified with add-to-position path)
        confidence = self.latest_signal_data.get('confidence', 'MEDIUM') if self.latest_signal_data else 'MEDIUM'
        validated = self._validate_sltp_for_entry(side, confidence)

        if validated is None:
            self.log.error(
                "❌ SL/TP validation failed for new position - order blocked "
                "(no price data, no signal data, or R/R < minimum)"
            )
            self._last_signal_status = {
                'executed': False,
                'reason': 'SL/TP验证失败，取消开仓',
                'action_taken': '',
            }
            if self.telegram_bot and self.enable_telegram:
                try:
                    self.telegram_bot.send_message_sync(
                        "🚫 开仓被阻止\n"
                        "SL/TP验证失败 (R/R不足或无价格数据)\n"
                        "维持 HOLD 等待下一信号\n\n"
                        f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
                    )
                except Exception as e:
                    self.log.debug(f"Telegram notification failure is non-critical: {e}")
                    pass  # Telegram notification failure is non-critical
            return

        stop_loss_price, tp_price, entry_price = validated

        # Log SL/TP summary
        self.log.info(
            f"🎯 Opening position for {side.name}:\n"
            f"   Entry: ${entry_price:,.2f} (LIMIT)\n"
            f"   Stop Loss: ${stop_loss_price:,.2f} ({((stop_loss_price/entry_price - 1) * 100):.2f}%)\n"
            f"   Take Profit: ${tp_price:,.2f} ({((tp_price/entry_price - 1) * 100):.2f}%)\n"
            f"   Quantity: {quantity:.3f}\n"
            f"   Confidence: {confidence}"
        )

        try:
            # v4.13: Two-phase approach - submit entry first, SL/TP after fill
            # Store pending SL/TP for on_position_opened() to submit
            self._pending_sltp = {
                'sl_price': stop_loss_price,
                'tp_price': tp_price,
                'entry_price': entry_price,
                'side': side,
                'quantity': quantity,
                'confidence': confidence,
            }

            # Pre-initialize sltp_state with estimated prices
            # (will be updated with actual fill price in on_position_opened)
            instrument_key = str(self.instrument_id)
            self.sltp_state[instrument_key] = {
                "entry_price": entry_price,
                "highest_price": entry_price,
                "lowest_price": entry_price,
                "current_sl_price": stop_loss_price,
                "current_tp_price": tp_price,
                # v6.0: Original validated SL/TP — never modified by emergency SL or TP adjustment.
                # Used by _captured_sltp_for_eval to record accurate planned R/R in trade memory.
                "validated_sl_price": stop_loss_price,
                "validated_tp_price": tp_price,
                "sl_order_id": None,  # Will be set in on_position_opened
                "side": "LONG" if side == OrderSide.BUY else "SHORT",
                "quantity": quantity,
            }

            # v4.17: Submit LIMIT entry order at validated entry_price
            # Instead of MARKET, use LIMIT at the exact price used for SL/TP calculation.
            # This guarantees fill_price == entry_price (or better), so R/R never degrades.
            # - If market is at or better than entry_price → fills immediately (taker)
            # - If market has moved unfavorably → sits on book (maker, lower fee)
            # - If unfilled by next on_timer → auto-cancelled, re-analyzed

            # Enforce Binance min notional AFTER make_qty() truncation.
            # make_qty() rounds DOWN to step_size (e.g. 0.001422 → 0.001),
            # which can drop notional below $100 minimum.
            _min_notional_val = float(self.instrument.min_notional) if self.instrument.min_notional else get_min_notional_usdt()
            min_notional = _min_notional_val * get_min_notional_safety_margin()
            _truncated_qty = float(self.instrument.make_qty(quantity))
            actual_notional = _truncated_qty * entry_price
            if actual_notional < min_notional and entry_price > 0:
                _step = float(self.instrument.size_increment)
                _step_inv = round(1.0 / _step)  # e.g. 0.001 → 1000
                min_qty = math.ceil(min_notional / entry_price * _step_inv) / _step_inv
                _min_notional_check = min_qty * entry_price
                if _min_notional_check < min_notional:
                    # Even after rounding up, still can't meet minimum — block order
                    self.log.warning(
                        f"🚫 开仓被拒: notional ${actual_notional:.0f} < "
                        f"${min_notional:.0f} 最低要求 (qty={_truncated_qty:.4f} BTC × ${entry_price:,.0f})"
                    )
                    self._last_signal_status = {
                        'executed': False,
                        'reason': f'Notional 不足 (${actual_notional:.0f} < ${min_notional:.0f})',
                        'action_taken': '',
                    }
                    return
                self.log.info(
                    f"📊 Notional ${actual_notional:.0f} < min ${min_notional:.0f}, "
                    f"qty {_truncated_qty:.3f} → {min_qty:.3f} BTC (step={_step})"
                )
                quantity = min_qty

            entry_order = self.order_factory.limit(
                instrument_id=self.instrument_id,
                order_side=side,
                quantity=self.instrument.make_qty(quantity),
                price=self.instrument.make_price(entry_price),
                time_in_force=TimeInForce.GTC,
                post_only=False,  # Allow immediate taker fill
            )
            self.submit_order(entry_order)

            # Track pending entry for on_timer cancellation
            self._pending_entry_order_id = str(entry_order.client_order_id)
            # v11.5: Count executed signal
            self._signals_executed_today = getattr(self, '_signals_executed_today', 0) + 1
            self._save_equity_snapshots()

            self.log.info(
                f"✅ Submitted LIMIT entry order: {side.name} {quantity:.3f} BTC @ ${entry_price:,.2f}\n"
                f"   Order ID: {str(entry_order.client_order_id)[:8]}...\n"
                f"   SL/TP will be submitted after fill (pending in _pending_sltp)"
            )

        except Exception as e:
            # Clear pending state on failure
            self._pending_sltp = None
            self._pending_entry_order_id = None
            self.log.error(f"❌ Failed to submit entry order: {e}")
            self.log.error(
                "🚫 NOT opening position without SL/TP protection - "
                "this would expose account to unlimited risk"
            )

            # Update signal status to indicate failure
            self._last_signal_status = {
                'executed': False,
                'reason': f'入场订单失败，取消开仓',
                'action_taken': '',
            }

            # Send CRITICAL Telegram alert
            if self.telegram_bot and self.enable_telegram:
                try:
                    error_msg = self.telegram_bot.format_error_alert({
                        'level': 'CRITICAL',
                        'message': (
                            f"🚫 入场订单失败，已取消开仓\n"
                            f"原因: 入场订单提交异常\n"
                            f"信号: {side.name} {quantity:.3f} BTC\n"
                            f"处理: 等待下一个信号"
                        ),
                        'context': f"错误: {str(e)}",
                    })
                    self.telegram_bot.send_message_sync(error_msg)
                except Exception as notify_error:
                    self.log.error(f"Failed to send Telegram alert: {notify_error}")

