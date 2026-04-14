"""
Safety Manager Mixin

Extracted from ai_strategy.py for code organization.
Contains emergency SL/TP handling, orphan order management,
position state clearing, and SL resubmission logic.
"""

import os
import json
import time
from typing import Dict, Any
from datetime import datetime, timedelta, timezone

from decimal import Decimal

from nautilus_trader.model.enums import OrderSide, OrderType, TriggerType, TrailingOffsetType
from nautilus_trader.model.position import Position


class SafetyManagerMixin:
    """Mixin providing safety and emergency order management for AITradingStrategy."""

    def _handle_orphan_order(self, order_id: str, reason: str):
        """
        v4.0 (A1): Clean up internal state when SL/TP orders become orphans.

        Called from on_order_expired(), on_order_rejected(), on_order_canceled().
        Checks if position still exists:
        - No position → clear all state (external close detected)
        - Position exists but no active SL → resubmit emergency SL

        v5.10: Bypass Binance account cache to avoid stale position data.
        When user manually closes position, the SL cancel event arrives before
        the cached account data refreshes, causing phantom position detection.

        v5.13/v7.2: Skip entirely when _sltp_modified_this_cycle is set.
        After per-layer SL replacement cancels old orders
        and submits new ones, the new SL may still be in SUBMITTED state (not
        yet ACK'd by Binance). on_order_rejected fires before the new SL appears
        in cache.orders_open() → false negative → spurious emergency SL.
        """
        try:
            # v5.13: If SL/TP were just submitted/replaced this cycle, skip orphan handling.
            # The new SL is likely still in SUBMITTED state (not yet in cache.orders_open()),
            # so _resubmit_sltp_if_needed would falsely conclude no active SL exists.
            # Real orphan detection resumes next on_timer cycle when flag is reset.
            if self._sltp_modified_this_cycle:
                self.log.info(
                    f"🔍 Orphan check skipped (SL/TP just replaced this cycle): "
                    f"{order_id[:8]}... reason={reason}"
                )
                return
            # v5.10: Invalidate Binance account cache BEFORE checking position.
            # Without this, get_positions() returns stale cached data (5s TTL)
            # that shows the old position even after it's been closed.
            if self.binance_account:
                try:
                    self.binance_account.get_account_info(use_cache=False)
                except Exception as e:
                    self.log.debug(f"Proceed with potentially stale data rather than crashing: {e}")
                    pass  # Proceed with potentially stale data rather than crashing

            current_position = self._get_current_position_data()

            if not current_position:
                self._clear_position_state()
                self.log.info(f"🧹 Position closed externally (orphan: {order_id[:8]}..., {reason})")
            else:
                self._resubmit_sltp_if_needed(current_position, reason)
        except Exception as e:
            self.log.warning(f"⚠️ _handle_orphan_order failed: {e}")

    def _clear_position_state(self):
        """
        v4.0 (A4): Clear all position-related internal state.

        Called when position is detected as closed externally (e.g., manual close
        on Binance app causing SL/TP to become orphans).
        """
        instrument_key = str(self.instrument_id)
        self.sltp_state.pop(instrument_key, None)
        self._pending_sltp = None
        self._pending_reversal = None
        with self._state_lock:
            self._manual_close_in_progress = False  # v13.0
        self.latest_signal_data = None
        # v7.2: Clear per-layer tracking
        self._layer_orders.clear()
        self._order_to_layer.clear()
        self._next_layer_idx = 0  # Reset monotonic counter on full clear
        self._persist_layer_orders()
        # v5.10: Reset emergency SL cooldown for next position
        self._emergency_sl_count = 0
        self._last_emergency_sl_time = 0.0
        self._emergency_retry_count = 0  # v18.0
        self._reduce_only_rejection_count = 0  # v18.2
        # v21.0: FR consecutive block counter is NOT reset here.
        # FR pressure is a market condition, not position state. Preserving it ensures
        # the exhaustion gate continues to protect against dead loops after position close.
        # v5.13: Clear intentional cancel tracking (stale IDs from closed position)
        self._intentionally_cancelled_order_ids.clear()
        # v48.0: Clear virtual DCA state
        self._dca_virtual_avg = 0
        self._dca_virtual_last_price = 0
        # v46.0: AI-era entry context vars removed (memory_conditions, ai_quality_score)
        # v36.3: Clear ghost detection flag. Without this, _ghost_first_seen can
        # remain set permanently when position closes via event (on_position_closed
        # clears _layer_orders → _has_stale_state becomes False → ghost detection
        # block is skipped → _ghost_first_seen never reaches its reset path).
        self._ghost_first_seen = 0.0
        # v18.3: Force analysis after external close (same as on_position_closed)
        self._force_analysis_cycles_remaining = 2
        self.log.info("🧹 Position state cleared (external close detected)")

    def _resubmit_sltp_if_needed(self, current_position: Dict[str, Any], reason: str = ""):
        """
        v4.0 (A4): Detect missing SL/TP and resubmit emergency SL if needed.

        Called when a SL/TP order expires/rejected/canceled but position still exists.
        Uses emergency SL (2% fixed) since current S/R data may be stale.

        v5.10: Added cooldown (120s) and max retry (3) to prevent infinite loop.
        Scenario: user manually closes position → Binance cancels SL → bot detects
        orphan → submits emergency SL → user cancels it → bot re-detects → loop.
        """
        try:
            position_side = current_position.get('side', '').lower()
            quantity = abs(float(current_position.get('quantity', 0)))
            if quantity <= 0 or position_side not in ('long', 'short'):
                return

            # v5.10: Cooldown check — prevent infinite emergency SL loop
            now = time.time()
            cooldown_sec = self.emergency_sl_cooldown_seconds
            max_consecutive = self.emergency_sl_max_consecutive

            if self._emergency_sl_count >= max_consecutive:
                self.log.warning(
                    f"⚠️ Emergency SL max retries ({max_consecutive}) reached. "
                    f"Stopping auto-resubmit. Manual intervention required."
                )
                if self.telegram_bot and self.enable_telegram:
                    try:
                        pos_cn = self.telegram_bot.side_to_cn(position_side, 'position')
                        with self._state_lock:
                            _price = self._cached_current_price or 0
                        qty_value = quantity * _price if _price > 0 else 0
                        qty_display = f"{quantity:.4f} BTC (${qty_value:,.0f})" if qty_value > 0 else f"{quantity:.4f} BTC"
                        self.telegram_bot.send_message_sync(
                            f"⚠️ 紧急止损已尝试 {max_consecutive} 次，停止自动重试。\n\n"
                            f"仓位: {qty_display} {pos_cn}\n"
                            f"请手动检查 Binance 仓位状态并设置止损。\n"
                            f"如仓位已平，请忽略此消息。\n\n"
                            f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
                        )
                    except Exception as e:
                        self.log.debug(f"Telegram notification failure is non-critical: {e}")
                        pass  # Telegram notification failure is non-critical
                return

            elapsed = now - self._last_emergency_sl_time
            if elapsed < cooldown_sec:
                remaining = cooldown_sec - elapsed
                self.log.info(
                    f"🔄 Emergency SL cooldown active ({remaining:.0f}s remaining), "
                    f"skipping resubmit (attempt {self._emergency_sl_count}/{max_consecutive})"
                )
                return

            # v7.2: Per-layer SL coverage check
            # v15.3: Query both NT cache + Binance Algo API to avoid missing orders
            open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
            open_order_ids = {str(o.client_order_id) for o in open_orders}

            # Supplement with Binance Algo API order IDs
            try:
                if self.binance_account:
                    symbol = str(self.instrument_id).split('.')[0].replace('-PERP', '')
                    algo_orders = self.binance_account.get_open_algo_orders(symbol)
                    for algo in algo_orders:
                        algo_status = algo.get('algoStatus', algo.get('status', ''))
                        if algo_status not in ('', 'WORKING', 'NEW'):
                            continue
                        algo_id = str(algo.get('clientOrderId', algo.get('algoId', '')))
                        if algo_id:
                            open_order_ids.add(algo_id)
            except Exception as e:
                self.log.debug(f"Algo API query in resubmit check (non-critical): {e}")

            emergency_qty = 0.0  # Actual quantity submitted to emergency SL

            if self._layer_orders:
                # Check each layer's SL — find uncovered quantity
                uncovered_qty = 0.0
                stale_layer_ids = []
                for layer_id, layer in list(self._layer_orders.items()):
                    sl_id = layer.get('sl_order_id')
                    trailing_id = layer.get('trailing_order_id')
                    # v24.2: Layer is protected if EITHER fixed SL or trailing is alive
                    has_fixed_sl = sl_id and sl_id in open_order_ids
                    has_trailing = trailing_id and trailing_id in open_order_ids
                    if not has_fixed_sl and not has_trailing:
                        uncovered_qty += layer.get('quantity', 0)
                        stale_layer_ids.append(layer_id)
                        self.log.warning(
                            f"⚠️ Layer {layer_id} unprotected "
                            f"(SL: {sl_id}, trailing: {trailing_id})"
                        )

                if uncovered_qty <= 0:
                    self.log.info("🔍 All layers have active SL orders, skipping resubmit")
                    return

                # Submit emergency SL for uncovered quantity
                self.log.warning(
                    f"⚠️ Uncovered qty: {uncovered_qty:.4f} after {reason}, "
                    f"submitting emergency SL"
                )
                self._submit_emergency_sl(uncovered_qty, position_side,
                                          reason=f"Layer SL missing ({reason})")
                emergency_qty = uncovered_qty

                # v15.5: Remove stale layers to prevent re-triggering on next check.
                # Emergency SL already created its own layer via _create_layer().
                # Without this cleanup, stale layers (sl_order_id=None) would be
                # re-detected as uncovered on the next cooldown cycle, causing
                # redundant emergency SL submissions up to max_consecutive times.
                for lid in stale_layer_ids:
                    removed = self._layer_orders.pop(lid, None)
                    if removed:
                        for key in ('sl_order_id', 'tp_order_id', 'trailing_order_id'):
                            oid = removed.get(key)
                            if oid and oid in self._order_to_layer:
                                del self._order_to_layer[oid]
                        self.log.info(
                            f"🧹 Stale layer {lid} removed (qty: {removed.get('quantity', 0):.4f})"
                        )
                self._persist_layer_orders()
            else:
                # No layer tracking — fallback: check if ANY active SL exists
                has_active_sl = any(
                    o.is_reduce_only and o.order_type in (
                        OrderType.STOP_MARKET, OrderType.TRAILING_STOP_MARKET,
                    )
                    for o in open_orders
                )
                if has_active_sl:
                    self.log.info("🔍 Active SL order found (no layer tracking), skipping resubmit")
                    return

                # No active SL → submit emergency SL for full quantity
                self.log.warning(f"⚠️ No active SL detected after {reason}, submitting emergency SL")
                self._submit_emergency_sl(quantity, position_side,
                                          reason=f"SL/TP orphan ({reason}), position still exists")
                emergency_qty = quantity

            # v5.10: Track cooldown state
            self._last_emergency_sl_time = now
            self._emergency_sl_count += 1

            # Send Telegram alert — use emergency_qty for accuracy
            if self.telegram_bot and self.enable_telegram:
                try:
                    side_cn = self.telegram_bot.side_to_cn(position_side, 'position')
                    context = f"方向: {side_cn}, Qty: {emergency_qty:.4f}"
                    if emergency_qty != quantity:
                        context += f" (总仓位: {quantity:.4f})"
                    context += f", 原因: {reason}"
                    alert_msg = self.telegram_bot.format_error_alert({
                        'level': 'CRITICAL',
                        'message': f"SL/TP 过期/被拒 — 已提交紧急止损",
                        'context': context,
                    })
                    self.telegram_bot.send_message_sync(alert_msg)
                except Exception as e:
                    self.log.debug(f"Telegram notification failure is non-critical: {e}")
                    pass  # Telegram notification failure is non-critical
        except Exception as e:
            self.log.error(f"❌ Failed to resubmit SL/TP: {e}")

    def _validate_sl_against_current_price(self, sl_price: float, side: str, current_price: float) -> float:
        """
        v4.0 (A2): Ensure SL won't immediately trigger against current price.

        This prevents Binance -2021 error (order would immediately trigger).
        If SL is on wrong side of current price, adjusts using ATR buffer.
        """
        if side.upper() in ('LONG', 'BUY') and sl_price >= current_price:
            buffer = self._cached_atr_value * 0.5 if self._cached_atr_value > 0 else current_price * 0.02
            sl_price = current_price - buffer
            self.log.warning(
                f"⚠️ SL adjusted: would immediately trigger for LONG. "
                f"New SL: ${sl_price:,.2f} (current: ${current_price:,.2f})"
            )
        elif side.upper() in ('SHORT', 'SELL') and sl_price <= current_price:
            buffer = self._cached_atr_value * 0.5 if self._cached_atr_value > 0 else current_price * 0.02
            sl_price = current_price + buffer
            self.log.warning(
                f"⚠️ SL adjusted: would immediately trigger for SHORT. "
                f"New SL: ${sl_price:,.2f} (current: ${current_price:,.2f})"
            )
        return sl_price

    def _submit_emergency_sl(self, quantity: float, position_side: str, reason: str):
        """
        v4.10: Submit emergency stop loss when normal SL is missing.

        This is a safety net - if SL update/recreate fails during scaling,
        we MUST have a stop loss to prevent unlimited losses.

        Uses 2% default stop loss from current price.

        Parameters
        ----------
        quantity : float
            Position quantity in BTC
        position_side : str
            Position side ('long' or 'short')
        reason : str
            Why emergency SL is needed (for logging/alert)
        """
        try:
            # Normalize position_side to lowercase — callers may pass uppercase
            # (e.g., event.side.name returns 'LONG'/'SHORT' from NautilusTrader enums)
            position_side = position_side.lower()

            # Get current price for emergency SL calculation
            current_price = None
            if self.binance_account:
                try:
                    current_price = self.binance_account.get_realtime_price('BTCUSDT')
                except Exception as e:
                    self.log.debug(f"Real-time price fetch is best-effort; cached price used as fallback: {e}")
                    pass  # Real-time price fetch is best-effort; cached price used as fallback

            if not current_price:
                with self._state_lock:
                    current_price = self._cached_current_price
                    price_age = time.time() - self._cached_current_price_time
                if current_price and price_age > self.price_cache_ttl_seconds:
                    self.log.warning(
                        f"⚠️ Cached price stale ({price_age:.0f}s > "
                        f"{self.price_cache_ttl_seconds}s TTL), using anyway for emergency SL"
                    )

            if not current_price or current_price <= 0:
                self.log.error(
                    f"🚨 CRITICAL: Cannot submit emergency SL - no price available! "
                    f"Position {quantity:.4f} BTC {position_side} is UNPROTECTED! "
                    f"Attempting market close as last resort."
                )
                # v6.1: Last resort — market close when no price available for SL
                self._emergency_market_close(quantity, position_side, reason)
                return

            # Phase 1.6: Emergency SL = max(base_pct, ATR×multiplier) — dynamic buffer
            # Fixed base_pct is too tight in high-volatility regimes; ATR×multiplier adapts to market.
            cached_atr = getattr(self, '_cached_atr_value', 0.0)
            if cached_atr > 0:  # current_price > 0 guaranteed by early return above
                emergency_sl_pct = max(self.emergency_sl_base_pct, (cached_atr / current_price) * self.emergency_sl_atr_multiplier)
            else:
                emergency_sl_pct = self.emergency_sl_base_pct
            if position_side == 'long':
                sl_price = current_price * (1 - emergency_sl_pct)
                exit_side = OrderSide.SELL
            else:
                sl_price = current_price * (1 + emergency_sl_pct)
                exit_side = OrderSide.BUY

            new_qty = self.instrument.make_qty(quantity)
            emergency_sl = self.order_factory.stop_market(
                instrument_id=self.instrument_id,
                order_side=exit_side,
                quantity=new_qty,
                trigger_price=self.instrument.make_price(sl_price),
                trigger_type=TriggerType.LAST_PRICE,
                reduce_only=True,
            )
            self.submit_order(emergency_sl)

            # Update trailing stop state
            instrument_key = str(self.instrument_id)
            if instrument_key in self.sltp_state:
                self.sltp_state[instrument_key]["sl_order_id"] = str(emergency_sl.client_order_id)
                self.sltp_state[instrument_key]["current_sl_price"] = sl_price
                self.sltp_state[instrument_key]["quantity"] = quantity

            # v7.3: Create a layer entry so emergency SL is visible to the
            # per-layer tracking system (_layer_orders, _order_to_layer, persistence).
            # Without this, emergency SL was invisible to layer system and lost on next restart.
            sl_order_id_str = str(emergency_sl.client_order_id)
            entry_price = current_price  # best estimate
            self._create_layer(
                entry_price=entry_price,
                quantity=quantity,
                side=position_side,
                sl_price=sl_price,
                tp_price=0,  # emergency SL has no corresponding TP
                sl_order_id=sl_order_id_str,
                tp_order_id="",
                confidence="EMERGENCY",
            )

            self.log.warning(
                f"🚨 Emergency SL submitted @ ${sl_price:,.2f} "
                f"({emergency_sl_pct*100:.2f}% from ${current_price:,.2f}, "
                f"ATR=${cached_atr:.0f})\n"
                f"   Reason: {reason}\n"
                f"   Quantity: {quantity:.4f} BTC {position_side}"
            )

            # v14.0: Log safety event for web dashboard
            self._log_safety_event({
                'type': 'emergency_sl',
                'reason': reason,
                'quantity': quantity,
                'side': position_side,
                'sl_price': sl_price,
                'current_price': current_price,
                'sl_pct': round(emergency_sl_pct * 100, 2),
                'atr': cached_atr,
                'order_id': sl_order_id_str,
            })

            # Send CRITICAL Telegram alert
            if self.telegram_bot and self.enable_telegram:
                try:
                    pos_cn = self.telegram_bot.side_to_cn(position_side, 'position')
                    qty_value = quantity * current_price if current_price > 0 else 0
                    qty_display = f"{quantity:.4f} BTC (${qty_value:,.0f})" if qty_value > 0 else f"{quantity:.4f} BTC"
                    self.telegram_bot.send_message_sync(
                        f"🚨 紧急止损已设置\n\n"
                        f"原因: {reason}\n"
                        f"仓位: {qty_display} {pos_cn}\n"
                        f"紧急SL: ${sl_price:,.2f} (距当前价 {emergency_sl_pct*100:.2f}%)\n"
                        f"当前价: ${current_price:,.2f} | ATR=${cached_atr:.0f}\n\n"
                        f"⚠️ 这是安全回退止损，请检查是否需要调整\n\n"
                        f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
                    )
                except Exception as e:
                    self.log.debug(f"Telegram notification failure is non-critical: {e}")
                    pass  # Telegram notification failure is non-critical

        except Exception as e:
            self.log.error(f"🚨 CRITICAL: Emergency SL submission failed: {e}")
            # v6.1: Fallback to market close when emergency SL fails
            self._emergency_market_close(quantity, position_side, f"{reason} + SL提交异常: {str(e)}")

    def _emergency_market_close(self, quantity: float, position_side: str, reason: str):
        """
        v6.1: Last-resort market close when emergency SL cannot be placed.

        This is the final safety net. If we cannot place ANY stop loss
        (no price, submission error, rate limit, etc.), we close the position
        at market to prevent unlimited losses.

        v7.1: Added retry with backoff (up to 3 attempts) and
        _needs_emergency_review flag for next on_timer cycle.

        Parameters
        ----------
        quantity : float
            Position quantity in BTC
        position_side : str
            Position side ('long' or 'short')
        reason : str
            Why market close is needed
        """
        self.log.error(
            f"🚨🚨 LAST RESORT: Market closing {quantity:.4f} BTC {position_side} — "
            f"all SL methods failed! Reason: {reason}"
        )

        # v14.1: Set forced close reason if not already set by caller (e.g., time barrier).
        # This ensures on_position_closed() shows 'EMERGENCY' instead of 'MANUAL'.
        if not self._forced_close_reason:
            self._forced_close_reason = ('EMERGENCY', f'🚨 紧急平仓: {reason}')

        # v24.0: Cancel outstanding reduce_only orders (SL/TP/trailing) before
        # market close to prevent quantity conflicts. If a trailing stop partially
        # filled, the remaining SL/TP orders at old quantities would cause -2022
        # ReduceOnly errors or overshoot the actual position size.
        try:
            open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
            cancelled_count = 0
            for order in open_orders:
                if order.is_reduce_only and order.is_open:
                    try:
                        self._intentionally_cancelled_order_ids.add(str(order.client_order_id))
                        self.cancel_order(order)
                        cancelled_count += 1
                    except Exception as cancel_err:
                        self.log.warning(
                            f"⚠️ Failed to cancel {order.order_type.name} "
                            f"{str(order.client_order_id)[:8]}... before emergency close: {cancel_err}"
                        )
            if cancelled_count > 0:
                self.log.info(
                    f"🧹 Cancelled {cancelled_count} reduce_only order(s) before emergency close"
                )
        except Exception as e:
            self.log.warning(f"⚠️ Failed to cancel orders before emergency close: {e}")

        # v7.1: Retry up to 3 times with short backoff
        max_attempts = 3
        submitted = False
        last_err = None

        for attempt in range(1, max_attempts + 1):
            try:
                exit_side = OrderSide.SELL if position_side == 'long' else OrderSide.BUY
                close_qty = self.instrument.make_qty(quantity)
                close_order = self.order_factory.market(
                    instrument_id=self.instrument_id,
                    order_side=exit_side,
                    quantity=close_qty,
                    reduce_only=True,
                )
                self.submit_order(close_order)
                self.log.warning(
                    f"🚨 Emergency MARKET CLOSE submitted (attempt {attempt}): "
                    f"{exit_side.name} {quantity:.4f} BTC reduce_only"
                )
                submitted = True
                break
            except Exception as close_err:
                last_err = close_err
                self.log.error(
                    f"🚨🚨🚨 Emergency market close attempt {attempt}/{max_attempts} failed: {close_err}"
                )
                if attempt < max_attempts:
                    time.sleep(0.5 * attempt)  # 0.5s, 1.0s backoff

        if not submitted:
            self.log.error(
                f"🚨🚨🚨 ALL PROTECTION FAILED after {max_attempts} attempts: {last_err}"
            )
            # v7.1: Flag for next on_timer to re-attempt protection
            self._needs_emergency_review = True
            # v14.1: Clear forced close reason — position didn't close,
            # prevent stale reason from corrupting future on_position_closed.
            self._forced_close_reason = None

            # v18.0: Register 30s one-shot retry instead of waiting 20 min
            self._emergency_retry_count += 1
            _max_retries = 5
            if self._emergency_retry_count <= _max_retries:
                import time as _time
                alert_name = f"emergency_retry_{int(_time.time())}"
                self.clock.set_time_alert(
                    name=alert_name,
                    alert_time=self.clock.utc_now() + timedelta(seconds=30),
                    callback=self._on_emergency_retry,
                )
                self.log.warning(
                    f"🚨 Emergency retry scheduled in 30s "
                    f"(attempt {self._emergency_retry_count}/{_max_retries})"
                )
            else:
                self.log.error(
                    f"🚨🚨🚨 Emergency retry exhausted ({_max_retries} attempts). "
                    f"Position UNPROTECTED. Manual intervention required."
                )
                if self.telegram_bot and self.enable_telegram:
                    try:
                        self.telegram_bot.send_message_sync(
                            f"🚨🚨🚨 紧急保护全部失败\n\n"
                            f"已尝试 {_max_retries} 次 (每次间隔 30s)\n"
                            f"仓位可能无任何保护!\n"
                            f"请立即手动处理 Binance 仓位!\n\n"
                            f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
                        )
                    except Exception as tg_err:
                        self.log.debug(f"Telegram notify failed in emergency_market_close: {tg_err}")

        # v14.0: Log safety event for web dashboard
        self._log_safety_event({
            'type': 'emergency_market_close',
            'reason': reason,
            'quantity': quantity,
            'side': position_side,
            'submitted': submitted,
            'attempts': max_attempts,
        })

        # Always send Telegram alert regardless of close success/failure
        if self.telegram_bot and self.enable_telegram:
            try:
                status = "已提交" if submitted else f"全部失败 ({max_attempts}次)"
                pos_cn = self.telegram_bot.side_to_cn(position_side, 'position')
                with self._state_lock:
                    _price = self._cached_current_price or 0
                qty_value = quantity * _price if _price > 0 else 0
                qty_display = f"{quantity:.4f} BTC (${qty_value:,.0f})" if qty_value > 0 else f"{quantity:.4f} BTC"
                self.telegram_bot.send_message_sync(
                    f"🚨🚨 紧急市价平仓\n\n"
                    f"原因: 所有止损方式均失败\n"
                    f"详情: {reason}\n"
                    f"仓位: {qty_display} {pos_cn}\n"
                    f"状态: {status}\n\n"
                    f"请立即检查账户!\n\n"
                    f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
                )
            except Exception as e:
                self.log.debug(f"Telegram notification failure is non-critical: {e}")
                pass  # Telegram notification failure is non-critical

    def _on_emergency_retry(self, event):
        """
        v18.0: Short-cycle emergency retry callback.

        Fires 30s after _emergency_market_close() fails.
        Uses existing _resubmit_sltp_if_needed() logic with position check.
        Pattern: v15.6 set_time_alert (same as force_analysis).
        """
        self.log.warning("🚨 Emergency retry timer fired — re-checking position protection")
        try:
            current_pos = self._get_current_position_data()
            if not current_pos:
                # Position already closed (liquidation, manual close, etc.)
                self.log.info("ℹ️ Emergency retry: no position found, clearing flags")
                self._needs_emergency_review = False
                self._emergency_retry_count = 0
                return

            # Use existing protection logic
            self._resubmit_sltp_if_needed(current_pos, "emergency_retry_30s")

            # Only clear review flag if all layers now have SL coverage.
            # _resubmit_sltp_if_needed may return early (cooldown/max retry)
            # without actually submitting any SL — clearing the flag in that
            # case would leave the position permanently unprotected.
            all_covered = True
            if self._layer_orders:
                open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
                open_order_ids = {str(o.client_order_id) for o in open_orders}
                for layer in self._layer_orders.values():
                    sl_id = layer.get('sl_order_id')
                    trailing_id = layer.get('trailing_order_id')
                    has_sl = sl_id and sl_id in open_order_ids
                    has_trailing = trailing_id and trailing_id in open_order_ids
                    if not has_sl and not has_trailing:
                        all_covered = False
                        break

            if all_covered:
                self._needs_emergency_review = False
                self.log.info("✅ Emergency retry: position protection restored")
            else:
                self.log.warning(
                    "⚠️ Emergency retry: protection still incomplete, "
                    "keeping _needs_emergency_review=True for next on_timer"
                )

        except Exception as e:
            self.log.error(f"🚨 Emergency retry failed: {e}")
            # _needs_emergency_review remains True for next on_timer

    def _log_safety_event(self, event: dict):
        """
        v14.0: Append a safety event to data/safety_events.json for web dashboard.

        Events include emergency SL triggers, market closes, and other safety actions.
        File is append-only, capped at 200 entries.
        """
        try:
            event['timestamp'] = datetime.now().isoformat()
            events_file = 'data/safety_events.json'
            os.makedirs('data', exist_ok=True)

            events = []
            if os.path.exists(events_file):
                try:
                    with open(events_file, 'r') as f:
                        events = json.load(f)
                except (json.JSONDecodeError, Exception):
                    self.log.debug("Using default value due to error")
                    events = []

            events.insert(0, event)
            events = events[:200]  # Cap at 200 entries

            with open(events_file, 'w') as f:
                json.dump(events, f, indent=2, default=str)
        except Exception as e:
            self.log.debug(f"Failed to log safety event: {e}")

    def _log_sltp_adjustment(self, event: dict):
        """
        v14.0: Append an SL/TP adjustment to data/sltp_adjustments.json for web dashboard.

        Tracks post-fill TP adjustments and other SL/TP changes.
        File is append-only, capped at 500 entries.
        """
        try:
            event['timestamp'] = datetime.now().isoformat()
            adj_file = 'data/sltp_adjustments.json'
            os.makedirs('data', exist_ok=True)

            adjustments = []
            if os.path.exists(adj_file):
                try:
                    with open(adj_file, 'r') as f:
                        adjustments = json.load(f)
                except (json.JSONDecodeError, Exception):
                    self.log.debug("Using default value due to error")
                    adjustments = []

            adjustments.insert(0, event)
            adjustments = adjustments[:500]  # Cap at 500 entries

            with open(adj_file, 'w') as f:
                json.dump(adjustments, f, indent=2, default=str)
        except Exception as e:
            self.log.debug(f"Failed to log SL/TP adjustment: {e}")

    def _recreate_sltp_after_reduce(self, remaining_qty: float, position_side: str):
        """
        v4.12: Recreate SL/TP orders after reducing position.

        After a reduce, all SL/TP were cancelled. This recalculates SL/TP using
        current S/R zones (via _validate_sltp_for_entry), with a safety rule:
        new SL must be at least as protective as the old SL.
        Falls back to emergency SL if recalculation fails.

        Parameters
        ----------
        remaining_qty : float
            Remaining position quantity after reduce
        position_side : str
            Position side ('long' or 'short')
        """
        try:
            instrument_key = str(self.instrument_id)
            state = self.sltp_state.get(instrument_key)
            old_sl_price = state.get("current_sl_price") if state else None
            exit_side = OrderSide.SELL if position_side == 'long' else OrderSide.BUY
            entry_side = OrderSide.BUY if position_side == 'long' else OrderSide.SELL
            new_qty = self.instrument.make_qty(remaining_qty)
            sl_submitted = False

            # v4.12: Recalculate SL/TP using current S/R zones
            confidence = self.latest_signal_data.get('confidence', 'MEDIUM') if self.latest_signal_data else 'MEDIUM'
            validated = self._validate_sltp_for_entry(entry_side, confidence)

            if validated:
                new_sl_price, new_tp_price, entry_price = validated

                # Safety rule: SL can only move in favorable direction
                # LONG: new SL >= old SL (higher is more protective)
                # SHORT: new SL <= old SL (lower is more protective)
                if old_sl_price and old_sl_price > 0:
                    if position_side == 'long':
                        final_sl = max(new_sl_price, old_sl_price)
                    else:
                        final_sl = min(new_sl_price, old_sl_price)
                    if final_sl != new_sl_price:
                        self.log.info(
                            f"🛡️ SL kept at more protective level: "
                            f"${final_sl:,.2f} (old) vs ${new_sl_price:,.2f} (new S/R)"
                        )
                    new_sl_price = final_sl

                sl_price = new_sl_price
                tp_price = new_tp_price
                self.log.info(
                    f"📊 Reduce recalc: SL=${sl_price:,.2f} TP=${tp_price:,.2f} "
                    f"(based on current S/R zones)"
                )
            else:
                # Fallback to old prices if recalculation fails
                sl_price = old_sl_price
                tp_price = state.get("current_tp_price") if state else None
                self.log.warning("⚠️ SL/TP recalculation failed, using previous prices")

            # Recreate SL
            if sl_price and sl_price > 0:
                try:
                    new_sl = self.order_factory.stop_market(
                        instrument_id=self.instrument_id,
                        order_side=exit_side,
                        quantity=new_qty,
                        trigger_price=self.instrument.make_price(sl_price),
                        trigger_type=TriggerType.LAST_PRICE,
                        reduce_only=True,
                    )
                    self.submit_order(new_sl)
                    sl_submitted = True
                    self.log.info(f"✅ Recreated SL @ ${sl_price:,.2f} for {remaining_qty:.4f} BTC")

                    if state:
                        state["sl_order_id"] = str(new_sl.client_order_id)
                        state["current_sl_price"] = sl_price
                        state["quantity"] = remaining_qty
                except Exception as e:
                    self.log.error(f"❌ Failed to recreate SL: {e}")

            # Recreate TP
            # v6.6: LIMIT_IF_TOUCHED → Binance TAKE_PROFIT (position-linked, auto-cancel)
            if tp_price and tp_price > 0:
                try:
                    new_tp = self.order_factory.limit_if_touched(
                        instrument_id=self.instrument_id,
                        order_side=exit_side,
                        quantity=new_qty,
                        price=self.instrument.make_price(tp_price),
                        trigger_price=self.instrument.make_price(tp_price),
                        trigger_type=TriggerType.LAST_PRICE,
                        reduce_only=True,
                    )
                    self.submit_order(new_tp)
                    self.log.info(f"✅ Recreated TP @ ${tp_price:,.2f} for {remaining_qty:.4f} BTC")

                    if state:
                        state["current_tp_price"] = tp_price
                except Exception as e:
                    self.log.error(f"❌ Failed to recreate TP: {e}")

            # v24.2: Resubmit trailing stop for the reduced quantity
            trailing_order_id = ""
            trailing_offset_bps = 0
            trailing_activation_price = 0.0
            if sl_submitted and sl_price and sl_price > 0:
                atr = getattr(self, '_cached_atr_4h', None) or getattr(self, '_cached_atr_value', None)
                entry_price_for_trailing = state.get("entry_price", 0) if state else 0
                risk = abs(entry_price_for_trailing - sl_price) if entry_price_for_trailing else 0
                if risk > 0 and atr and atr > 0 and entry_price_for_trailing > 0:
                    try:
                        activation_r = getattr(self, '_TRAILING_ACTIVATION_R', 1.5)
                        trailing_atr_mult = getattr(self, '_TRAILING_ATR_MULTIPLIER', 0.6)
                        if position_side == 'long':
                            act_price = entry_price_for_trailing + (risk * activation_r)
                        else:
                            act_price = entry_price_for_trailing - (risk * activation_r)
                        t_distance = atr * trailing_atr_mult
                        t_bps = int((t_distance / entry_price_for_trailing) * 10000)
                        t_bps = max(10, min(1000, t_bps))

                        trailing_order = self.order_factory.trailing_stop_market(
                            instrument_id=self.instrument_id,
                            order_side=exit_side,
                            quantity=new_qty,
                            trigger_price=None,
                            trigger_type=TriggerType.LAST_PRICE,
                            trailing_offset=Decimal(str(t_bps)),
                            trailing_offset_type=TrailingOffsetType.BASIS_POINTS,
                            activation_price=self.instrument.make_price(act_price),
                            reduce_only=True,
                        )
                        self.submit_order(trailing_order)
                        trailing_order_id = str(trailing_order.client_order_id)
                        trailing_offset_bps = t_bps
                        trailing_activation_price = act_price
                        self.log.info(
                            f"✅ Recreated trailing SL: activation @ ${act_price:,.2f}, "
                            f"callback {t_bps / 100:.1f}%"
                        )
                    except Exception as e:
                        self.log.warning(f"⚠️ Trailing SL recreation failed (non-critical): {e}")

            # v5.13: Mark SL/TP as modified — prevent orphan cascade from async cancel
            if sl_submitted:
                self._sltp_modified_this_cycle = True

            # If SL failed, use emergency SL
            if not sl_submitted:
                self._submit_emergency_sl(remaining_qty, position_side, reason="减仓后SL重建失败")
                # _submit_emergency_sl calls _create_layer internally, no further rebuild needed
            else:
                # Rebuild _layer_orders: consolidate into single layer with new order IDs
                # Old layers are stale (their SL/TP were cancelled above), replace with one layer
                self._layer_orders.clear()
                self._order_to_layer.clear()
                self._next_layer_idx = 0
                # Extract order IDs from sltp_state (updated above on success)
                sl_id_str = state.get("sl_order_id", "") if state else ""
                tp_id_str = ""
                if state and tp_price and tp_price > 0:
                    # TP order ID is not stored in sltp_state, but new_tp is in scope
                    # when sl_submitted=True and tp_price > 0
                    try:
                        tp_id_str = str(new_tp.client_order_id)
                    except NameError:
                        pass  # TP submission failed, no order ID
                entry_price_est = state.get("entry_price", 0) if state else 0
                self._create_layer(
                    entry_price=entry_price_est,
                    quantity=remaining_qty,
                    side=position_side,
                    sl_price=sl_price,
                    tp_price=tp_price or 0,
                    sl_order_id=sl_id_str,
                    tp_order_id=tp_id_str,
                    confidence=confidence,
                    trailing_order_id=trailing_order_id,
                    trailing_offset_bps=trailing_offset_bps,
                    trailing_activation_price=trailing_activation_price,
                )
                self.log.info(
                    f"📊 Layer tracking rebuilt after reduce: 1 consolidated layer "
                    f"({remaining_qty:.4f} BTC, SL ${sl_price:,.2f})"
                )

        except Exception as e:
            self.log.error(f"❌ Failed to recreate SL/TP after reduce: {e}")
            self._submit_emergency_sl(remaining_qty, position_side, reason=f"减仓后SL/TP重建异常: {str(e)}")

    # =========================================================================
    # v36.4: In-Session TP Coverage Check
    # =========================================================================

    def _check_tp_coverage(self):
        """
        v36.4: Check all layers for missing TP orders and resubmit.

        Unlike Tier 2 recovery (startup only), this runs every on_timer cycle
        when a position is held. Catches TP submission failures that occurred
        during on_position_opened (both retry attempts failed).

        Only resubmits if:
        - Layer has tp_price > 0 but tp_order_id is empty
        - TP hasn't been passed (still on profitable side of current price)
        """
        if not self._layer_orders:
            return

        for layer_id, layer in list(self._layer_orders.items()):
            tp_id = layer.get('tp_order_id', '')
            tp_price = layer.get('tp_price', 0)

            if not tp_id and tp_price > 0:
                # Rate-limit: skip if TP resubmit was already attempted this cycle
                _tp_resubmit_key = f"_tp_resubmit_attempted_{layer_id}"
                if getattr(self, _tp_resubmit_key, False):
                    continue
                setattr(self, _tp_resubmit_key, True)
                self.log.warning(
                    f"⚠️ Layer {layer.get('layer_index', '?')} has no TP order "
                    f"(tp_price=${tp_price:,.2f}) — attempting in-session resubmit"
                )
                self._resubmit_tp_for_layer(layer_id, tp_price)

    # =========================================================================
    # v24.0: Emergency TP Resubmission
    # =========================================================================

    def _resubmit_tp_for_layer(self, layer_id: str, original_tp_price: float):
        """
        v24.0: Resubmit TP order for a layer whose TP was canceled/expired.

        Unlike emergency SL (which uses a fallback 2% price), this resubmits at
        the ORIGINAL planned TP price from calculate_mechanical_sltp(). The TP
        price is specific to this entry's R/R calculation and should not change.

        Validation: TP must still be on the profitable side of current price.
        If TP price has been passed (opportunity missed), no resubmit.

        Parameters
        ----------
        layer_id : str
            The layer whose TP needs recovery
        original_tp_price : float
            The original planned TP price (before it was cleared to 0)
        """
        try:
            layer = self._layer_orders.get(layer_id)
            if not layer:
                self.log.debug(f"TP resubmit skipped: {layer_id} not found")
                return

            side = layer.get('side', '').lower()
            quantity = layer.get('quantity', 0)
            if quantity <= 0 or side not in ('long', 'short'):
                return

            if original_tp_price <= 0:
                self.log.warning(f"⚠️ Cannot resubmit TP for {layer_id}: no original TP price")
                return

            # Get current price to validate TP is still reachable
            current_price = None
            if self.binance_account:
                try:
                    current_price = self.binance_account.get_realtime_price('BTCUSDT')
                except Exception as e:
                    self.log.debug(f"get_realtime_price fallback to cache: {e}")
            if not current_price:
                with self._state_lock:
                    current_price = self._cached_current_price

            if not current_price or current_price <= 0:
                self.log.warning(f"⚠️ Cannot resubmit TP for {layer_id}: no price available")
                return

            # Validate TP is still on profitable side (not already passed through)
            if side == 'long' and original_tp_price <= current_price:
                self.log.info(
                    f"ℹ️ TP for {layer_id} already passed "
                    f"(TP ${original_tp_price:,.2f} <= current ${current_price:,.2f}), "
                    f"skipping resubmit"
                )
                return
            elif side == 'short' and original_tp_price >= current_price:
                self.log.info(
                    f"ℹ️ TP for {layer_id} already passed "
                    f"(TP ${original_tp_price:,.2f} >= current ${current_price:,.2f}), "
                    f"skipping resubmit"
                )
                return

            # Submit new TP order at original planned price
            exit_side = OrderSide.SELL if side == 'long' else OrderSide.BUY
            tp_order = self.order_factory.limit_if_touched(
                instrument_id=self.instrument_id,
                order_side=exit_side,
                quantity=self.instrument.make_qty(quantity),
                price=self.instrument.make_price(original_tp_price),
                trigger_price=self.instrument.make_price(original_tp_price),
                trigger_type=TriggerType.LAST_PRICE,
                reduce_only=True,
            )
            self.submit_order(tp_order)

            # Update layer tracking with new TP order
            new_tp_id = str(tp_order.client_order_id)
            layer['tp_order_id'] = new_tp_id
            layer['tp_price'] = original_tp_price
            self._order_to_layer[new_tp_id] = layer_id
            self._update_aggregate_sltp_state()
            self._persist_layer_orders()

            self.log.warning(
                f"🎯 TP resubmitted for {layer_id} @ ${original_tp_price:,.2f} "
                f"(current: ${current_price:,.2f}, qty: {quantity:.4f} BTC {side})"
            )

            # Log safety event for web dashboard
            self._log_safety_event({
                'type': 'tp_resubmit',
                'layer_id': layer_id,
                'tp_price': original_tp_price,
                'current_price': current_price,
                'quantity': quantity,
                'side': side,
                'order_id': new_tp_id,
            })

            # Telegram notification (private chat — operational alert)
            if self.telegram_bot and self.enable_telegram:
                try:
                    pos_cn = self.telegram_bot.side_to_cn(side, 'position')
                    self.telegram_bot.send_message_sync(
                        f"🎯 止盈单已恢复\n\n"
                        f"层: {layer_id}\n"
                        f"仓位: {quantity:.4f} BTC {pos_cn}\n"
                        f"止盈价: ${original_tp_price:,.2f}\n"
                        f"当前价: ${current_price:,.2f}\n\n"
                        f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
                    )
                except Exception as e:
                    self.log.debug(f"TP resubmit notification failed (non-critical): {e}")

        except Exception as e:
            self.log.error(f"❌ Failed to resubmit TP for {layer_id}: {e}")

    # =========================================================================
    # v11.0: Time Barrier (Triple Barrier 3rd Layer)
    # =========================================================================

