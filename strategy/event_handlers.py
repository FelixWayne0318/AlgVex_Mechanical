"""
NautilusTrader Event Handler Mixin

Extracted from ai_strategy.py for code organization.
Contains all NautilusTrader event callback methods:
on_order_filled, on_order_rejected, on_position_opened,
on_position_closed, on_order_canceled, on_order_expired,
on_order_denied, on_position_changed, and cleanup helpers.
"""

import time
from typing import Dict, Any
from datetime import datetime, timezone

from decimal import Decimal

from nautilus_trader.model.enums import OrderSide, OrderType, PositionSide, TriggerType, TrailingOffsetType
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.position import Position

from strategy.trading_logic import evaluate_trade, get_min_rr_ratio
from utils.risk_controller import TradingState


class EventHandlersMixin:
    """Mixin providing NautilusTrader event handler methods for AITradingStrategy."""

    def on_order_filled(self, event):
        """
        Handle order filled events.

        v7.2: Per-layer OCO — when a layer's SL/TP fills, cancel only THAT layer's peer.
        If it's the last layer, remaining GTE_GTC orders auto-cancel on position close.
        """
        filled_order_id = str(event.client_order_id)

        self.log.info(
            f"✅ Order filled: {event.order_side.name} "
            f"{float(event.last_qty)} @ {float(event.last_px)} "
            f"(ID: {filled_order_id[:8]}...)"
        )

        # v7.2: Per-layer OCO peer cancellation
        try:
            filled_order = self.cache.order(event.client_order_id)
            if filled_order and filled_order.is_reduce_only:
                # Look up which layer this order belongs to
                layer_id = self._order_to_layer.get(filled_order_id)

                if layer_id and layer_id in self._layer_orders:
                    layer = self._layer_orders[layer_id]
                    is_sl = (filled_order.order_type in (
                        OrderType.STOP_MARKET,
                        OrderType.TRAILING_STOP_MARKET,
                    ))

                    # v24.2: Cancel ALL counterpart orders for this layer.
                    # With SL + trailing + TP, filling one must cancel the other two.
                    peer_keys = []
                    if is_sl:
                        peer_keys = ['tp_order_id', 'trailing_order_id']
                    else:
                        # TP filled — cancel both SL and trailing
                        peer_keys = ['sl_order_id', 'trailing_order_id']

                    for peer_key in peer_keys:
                        peer_id = layer.get(peer_key, '')
                        # Don't cancel the order that just filled
                        if peer_id and peer_id != filled_order_id:
                            try:
                                peer_order = self.cache.order(ClientOrderId(peer_id))
                                if peer_order and peer_order.is_open:
                                    self._intentionally_cancelled_order_ids.add(peer_id)
                                    self.cancel_order(peer_order)
                                    label = peer_key.replace('_order_id', '').upper()
                                    self.log.info(
                                        f"🔗 Cancelled Layer {layer.get('layer_index', '?')} "
                                        f"{label} peer: {peer_id[:8]}..."
                                    )
                            except Exception as e:
                                self.log.warning(f"⚠️ Failed to cancel layer peer: {e}")

                    # Remove this layer from tracking
                    exit_price = float(event.last_px)
                    exit_type = 'SL' if is_sl else 'TP'
                    self.log.info(
                        f"📊 Layer {layer.get('layer_index', '?')} closed by {exit_type} "
                        f"@ ${exit_price:,.2f} (entry ${layer['entry_price']:,.2f})"
                    )

                    # Set forced close reason for on_position_closed().
                    # This is authoritative — the layer system knows exactly
                    # whether this was SL or TP, no price-tolerance guessing.
                    # Only set if not already set (emergency/time_barrier/reversal
                    # take priority). Subsequent layer fills overwrite (last wins).
                    if not self._forced_close_reason:
                        if is_sl:
                            sl_price = layer.get('sl_price', exit_price)
                            # v24.2-fix: Use order_type to detect trailing stop fill.
                            # filled_order.order_type is authoritative — no reliance
                            # on layer flags which may not be set.
                            is_trailing = (
                                filled_order.order_type == OrderType.TRAILING_STOP_MARKET
                            )
                            if is_trailing:
                                self._forced_close_reason = (
                                    'TRAILING_STOP',
                                    f'🔄 追踪止损触发 (Trailing SL: ${exit_price:,.2f})',
                                )
                            else:
                                self._forced_close_reason = (
                                    'STOP_LOSS',
                                    f'🛑 止损触发 (SL: ${sl_price:,.2f})',
                                )
                        else:
                            tp_price = layer.get('tp_price', exit_price)
                            self._forced_close_reason = (
                                'TAKE_PROFIT',
                                f'🎯 止盈触发 (TP: ${tp_price:,.2f})',
                            )

                    self._remove_layer(layer_id)
                else:
                    # v14.1: Only use legacy OCO cleanup when layer tracking is empty
                    # (pre-v7.2 compatibility). When layers exist but fill doesn't match,
                    # do NOT cancel — it would destroy other layers' SL/TP protection.
                    # v30.5: Infer close reason from order_type even without layer tracking.
                    # order_type is authoritative — STOP_MARKET/TRAILING_STOP_MARKET = SL,
                    # LIMIT_IF_TOUCHED (TAKE_PROFIT) = TP, regardless of layer mapping.
                    if not self._forced_close_reason:
                        exit_price_fill = float(event.last_px)
                        if filled_order.order_type == OrderType.TRAILING_STOP_MARKET:
                            self._forced_close_reason = (
                                'TRAILING_STOP',
                                f'🔄 追踪止损触发 (Trailing SL: ${exit_price_fill:,.2f})',
                            )
                        elif filled_order.order_type == OrderType.STOP_MARKET:
                            self._forced_close_reason = (
                                'STOP_LOSS',
                                f'🛑 止损触发 (SL: ${exit_price_fill:,.2f})',
                            )
                        elif filled_order.order_type == OrderType.LIMIT_IF_TOUCHED:
                            self._forced_close_reason = (
                                'TAKE_PROFIT',
                                f'🎯 止盈触发 (TP: ${exit_price_fill:,.2f})',
                            )
                        else:
                            # MARKET, LIMIT, or other order types — not SL/TP,
                            # likely manual close, emergency close, or liquidation.
                            self._forced_close_reason = (
                                'MANUAL',
                                f'手动/其他平仓 @ ${exit_price_fill:,.2f}',
                            )
                        self.log.info(
                            f"📊 Close reason inferred from order_type "
                            f"({filled_order.order_type.name}): "
                            f"{self._forced_close_reason[0]}"
                        )

                    if not self._layer_orders:
                        # Legacy: cancel all reduce_only peers (no layer tracking active)
                        open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
                        peer_orders = [o for o in open_orders
                                       if o.is_reduce_only and o.client_order_id != event.client_order_id]
                        for peer in peer_orders:
                            try:
                                self.cancel_order(peer)
                                self.log.info(
                                    f"🔗 Cancelled OCO peer (legacy): {str(peer.client_order_id)[:8]}..."
                                )
                            except Exception as e:
                                self.log.warning(f"⚠️ Failed to cancel OCO peer: {e}")
                    else:
                        self.log.warning(
                            f"⚠️ Filled reduce_only order {filled_order_id[:8]}... not in layer tracking "
                            f"({len(self._layer_orders)} layers active). "
                            f"Skipping OCO cleanup to protect other layers' SL/TP."
                        )
        except Exception as e:
            self.log.debug(f"OCO peer cleanup check: {e}")

        # v14.1: Broadcast /partial_close fill to subscribers.
        # _pending_partial_close_broadcast is set in _cmd_partial_close() and consumed here.
        # Only fires for non-layer reduce_only fills (layer SL/TP handled above).
        # Thread safety: optimistic check under lock, then atomic consume under lock.
        with self._state_lock:
            _has_pending_broadcast = self._pending_partial_close_broadcast is not None
        if _has_pending_broadcast:
            try:
                filled_order = self.cache.order(event.client_order_id)
                if filled_order and filled_order.is_reduce_only:
                    layer_id = self._order_to_layer.get(filled_order_id)
                    if not layer_id:
                        with self._state_lock:
                            info = self._pending_partial_close_broadcast
                            self._pending_partial_close_broadcast = None
                        if info and self.telegram_bot and self.enable_telegram:
                            with self._state_lock:
                                cached_price = self._cached_current_price
                            remaining_qty = info['old_qty'] - info['close_qty']
                            scaling_msg = self.telegram_bot.format_scaling_notification({
                                'action': 'REDUCE',
                                'side': info['side'],
                                'old_qty': info['old_qty'],
                                'new_qty': remaining_qty,
                                'change_qty': info['close_qty'],
                                'current_price': cached_price,
                            })
                            self.telegram_bot.send_message_sync(scaling_msg, broadcast=True)
                            self.log.info(
                                f"📱 Partial close broadcast sent to subscribers "
                                f"({info['pct']}% = {info['close_qty']:.4f} BTC)"
                            )
            except Exception as e:
                self.log.debug(f"Partial close broadcast failed (non-critical): {e}")
                with self._state_lock:
                    self._pending_partial_close_broadcast = None

    def on_order_rejected(self, event):
        """
        Handle order rejected events.

        🚨 Fix G34: Send critical Telegram alert for order rejections.
        v4.0 (A1): Call _handle_orphan_order to detect unprotected positions.
        v18.1: Handle -2022 ReduceOnly rejection — position already closed externally.
        """
        reason = getattr(event, 'reason', 'Unknown reason')
        client_order_id = str(getattr(event, 'client_order_id', 'N/A'))

        self.log.error(f"❌ Order rejected: {reason}")

        # v18.1: -2022 ReduceOnly rejection = position no longer exists on Binance.
        # This happens when user manually closes on Binance app/web and the bot
        # tries to submit reduce_only orders (emergency close, SL, TP) against
        # a position that no longer exists. Clear state immediately to stop the
        # time barrier → emergency close → rejection → resubmit loop.
        is_reduce_only_rejection = '-2022' in str(reason) or 'ReduceOnly' in str(reason)
        if not is_reduce_only_rejection:
            self._reduce_only_rejection_count = 0  # v18.2: reset consecutive counter
        if is_reduce_only_rejection:
            self._reduce_only_rejection_count += 1
            self.log.warning(
                f"⚠️ -2022 ReduceOnly rejection #{self._reduce_only_rejection_count} — "
                f"position likely closed externally. Verifying with Binance API..."
            )

            # v18.2: Counter-based failsafe — after 3 consecutive -2022 rejections,
            # force clear state even if Binance API verification fails.
            # This breaks the ghost position loop that ran 4.5h on 2026-02-26.
            _max_rejections_before_force_clear = 3
            force_clear = self._reduce_only_rejection_count >= _max_rejections_before_force_clear

            try:
                # Force fresh API call (bypass cache)
                if self.binance_account:
                    self.binance_account.get_account_info(use_cache=False)
                symbol = str(self.instrument_id).split('.')[0].replace('-PERP', '')
                binance_positions = self.binance_account.get_positions(symbol)
                if binance_positions is not None and len(binance_positions) == 0:
                    # Confirmed: no position on exchange
                    self.log.warning(
                        "✅ Binance confirms NO position. Clearing stale internal state. "
                        "This was likely a manual close on Binance app/web."
                    )
                    self._clear_position_state()
                    self._needs_emergency_review = False
                    # Suppress the noisy Telegram alert — send a clean resolution message instead
                    if self.telegram_bot and self.enable_telegram:
                        try:
                            self.telegram_bot.send_message_sync(
                                "ℹ️ 仓位已确认平仓 (手动平仓)\n\n"
                                "Binance API 确认无持仓，已清理内部状态。\n"
                                "后续 -2022 告警可忽略。"
                            )
                        except Exception as e:
                            self.log.debug(f"-2022 recovery Telegram notification failed: {e}")
                    return  # Skip normal rejection handling — state is already clean
                elif force_clear:
                    # API returned positions but we've had 3+ consecutive rejections.
                    # Binance says position exists but rejects ReduceOnly — contradictory.
                    # Force clear to break the loop.
                    self.log.warning(
                        f"⚠️ Force clearing after {self._reduce_only_rejection_count} consecutive "
                        f"-2022 rejections (Binance returned positions but rejects ReduceOnly). "
                        f"Breaking ghost position loop."
                    )
                    self._clear_position_state()
                    self._needs_emergency_review = False
                    if self.telegram_bot and self.enable_telegram:
                        try:
                            self.telegram_bot.send_message_sync(
                                f"⚠️ 幽灵仓位检测 — 强制清理\n\n"
                                f"连续 {self._reduce_only_rejection_count} 次 ReduceOnly 被拒绝。\n"
                                f"已强制清理内部状态，打断死循环。\n"
                                f"请检查币安账户确认仓位状态。"
                            )
                        except Exception as e:
                            self.log.debug(f"-2022 force clear Telegram failed: {e}")
                    return
            except Exception as e:
                self.log.warning(f"⚠️ -2022 Binance verification failed: {e}.")
                if force_clear:
                    # v18.2: Even if API fails, force clear after N rejections.
                    self.log.warning(
                        f"🚨 Force clearing after {self._reduce_only_rejection_count} consecutive "
                        f"-2022 rejections (Binance API also failed: {e}). "
                        f"Breaking ghost position loop."
                    )
                    self._clear_position_state()
                    self._needs_emergency_review = False
                    if self.telegram_bot and self.enable_telegram:
                        try:
                            self.telegram_bot.send_message_sync(
                                f"🚨 幽灵仓位检测 — 强制清理\n\n"
                                f"连续 {self._reduce_only_rejection_count} 次 ReduceOnly 被拒绝，\n"
                                f"且 Binance API 验证失败。\n"
                                f"已强制清理内部状态。\n"
                                f"请立即检查币安账户！"
                            )
                        except Exception as tg_e:
                            self.log.debug(f"-2022 force clear Telegram failed: {tg_e}")
                    return
                else:
                    self.log.warning("Proceeding with normal rejection flow.")

        # v4.17: If the rejected order is our pending LIMIT entry, clean up state
        if self._pending_entry_order_id and client_order_id == self._pending_entry_order_id:
            self.log.warning(f"⚠️ LIMIT entry order rejected: {reason}")
            self._pending_entry_order_id = None
            self._pending_sltp = None
            # Clear pre-initialized sltp_state
            instrument_key = str(self.instrument_id)
            if instrument_key in self.sltp_state:
                state = self.sltp_state[instrument_key]
                if state.get("sl_order_id") is None:
                    del self.sltp_state[instrument_key]

        # Clear dedup fingerprint when an entry/add-on order is rejected.
        # The fingerprint was saved optimistically in _execute_trade (executed=True),
        # but Binance rejected the entry asynchronously. Without clearing, the same
        # signal is treated as "duplicate" for the rest of the session.
        try:
            _order = self.cache.order(event.client_order_id)
            if _order and not _order.is_reduce_only:
                if self._last_executed_fingerprint:
                    self.log.info(
                        f"🔓 Cleared fingerprint '{self._last_executed_fingerprint}' "
                        f"(entry order rejected by exchange)"
                    )
                    self._last_executed_fingerprint = ""
        except Exception:
            pass  # Cache lookup failure is non-critical

        # 🚨 Fix G34: Force Telegram alert for order rejections
        if self.telegram_bot and self.enable_telegram:
            try:
                alert_msg = self.telegram_bot.format_error_alert({
                    'level': 'CRITICAL',
                    'message': f"订单被拒: {reason}",
                    'context': f"订单 ID: {client_order_id}",
                })
                self.telegram_bot.send_message_sync(alert_msg)
                self.log.info("📱 Telegram alert sent for order rejection")
            except Exception as e:
                self.log.warning(f"Failed to send Telegram alert for order rejection: {e}")

        # v4.0 (A1): Check if position is now unprotected
        # If the rejected order was an SL we just submitted this cycle,
        # clear the flag so _handle_orphan_order can detect the naked position.
        _layer_id = self._order_to_layer.get(client_order_id)
        if _layer_id and _layer_id in self._layer_orders:
            _layer = self._layer_orders[_layer_id]
            if _layer.get('sl_order_id') == client_order_id:
                self._sltp_modified_this_cycle = False
                self.log.warning(
                    f"⚠️ SL order rejected for {_layer_id} — clearing _sltp_modified_this_cycle "
                    f"to enable immediate orphan detection"
                )
        self._handle_orphan_order(client_order_id, f"rejected: {reason}")

    def on_position_opened(self, event):
        """
        Handle position opened events.

        v4.13: Submit SL/TP as individual orders after entry fill.
        v7.2: Create layer 0 with independent SL/TP (per-layer architecture).
        v13.0: Guard against phantom position opens during manual close.
        """
        self.log.info(
            f"🟢 Position opened: {event.side.name} "
            f"{float(event.quantity)} @ {float(event.avg_px_open)}"
        )

        # v13.0: Detect phantom position open caused by NautilusTrader position state mismatch.
        # After restart, NT may not have the position tracked internally. When a reduce_only
        # close order fills on Binance, NT interprets the fill as opening a NEW position.
        # Guard: if _manual_close_in_progress is set, verify with Binance before proceeding.
        with self._state_lock:
            _manual_close_active = self._manual_close_in_progress
            if _manual_close_active:
                self._manual_close_in_progress = False  # Reset flag immediately
        if _manual_close_active:
            try:
                binance_pos = self._get_current_position_data(from_telegram=True)
                if not binance_pos or binance_pos.get('quantity', 0) == 0:
                    self.log.warning(
                        f"⚠️ PHANTOM POSITION DETECTED: on_position_opened fired for "
                        f"{event.side.name} {float(event.quantity)} BTC but Binance shows "
                        f"NO position. This is a NautilusTrader internal state mismatch "
                        f"after restart. Ignoring — no SL/TP will be submitted."
                    )
                    # Clear any stale state that could confuse later logic
                    self._layer_orders.clear()
                    self._order_to_layer.clear()
                    self._next_layer_idx = 0
                    self._ghost_first_seen = 0.0  # v36.3
                    self._persist_layer_orders()
                    instrument_key = str(self.instrument_id)
                    self.sltp_state.pop(instrument_key, None)
                    self._pending_sltp = None
                    return
                else:
                    self.log.info(
                        f"ℹ️ Manual close produced position change — Binance confirms "
                        f"{binance_pos['side']} {binance_pos['quantity']:.4f} BTC. Proceeding."
                    )
            except Exception as e:
                self.log.warning(f"⚠️ Phantom position check failed: {e}. Proceeding with caution.")

        # v4.17: Clear pending entry tracking — order has filled
        self._pending_entry_order_id = None

        # v10.0: Mechanical mode — reset direction lock on successful entry
        if getattr(self, '_strategy_mode', 'ai') == 'mechanical':
            try:
                from agents.mechanical_decide import reset_direction_lock
                reset_direction_lock(event.side.name)  # BUY or SELL
            except Exception:
                pass

        # v3.12: Store entry conditions for memory system
        self._last_entry_conditions = self._format_entry_conditions()

        # v5.1: Store entry timestamp for trade evaluation (hold duration)
        self._last_entry_timestamp = datetime.now(timezone.utc).isoformat()

        # v46.0: Mechanical mode — no AI judge/debate/timing data to capture.
        # These fields were used by AI reflection system (deleted in v46.0).
        self._entry_key_lesson_tags = []

        instrument_key = str(self.instrument_id)
        entry_price = float(event.avg_px_open)

        # v11.5: Initialize MAE/MFE tracking
        self._position_entry_price_for_mae = entry_price
        self._position_max_price = entry_price
        self._position_min_price = entry_price
        self._position_is_long_for_mae = event.side == PositionSide.LONG

        # v7.2: Clear layer state for new position (fresh start)
        self._layer_orders.clear()
        self._order_to_layer.clear()
        self._next_layer_idx = 0

        # v4.13: Submit pending SL/TP orders individually → create as layer 0
        if hasattr(self, '_pending_sltp') and self._pending_sltp:
            pending = self._pending_sltp
            self._pending_sltp = None  # Clear immediately to prevent double submission

            sl_price = pending['sl_price']
            tp_price = pending['tp_price']
            quantity = float(event.quantity)

            # v4.0 (A2): Validate SL against current price to prevent -2021 immediate trigger
            side_name = event.side.name  # "LONG" or "SHORT"
            sl_price = self._validate_sl_against_current_price(sl_price, side_name, entry_price)

            # v4.16: Pre-adjust TP using actual fill price BEFORE submitting
            tp_price, tp_adjusted = self._adjust_tp_for_fill_price(
                tp_price=tp_price,
                sl_price=sl_price,
                fill_price=entry_price,
                side=side_name,
            )

            # Determine exit side (opposite of position)
            exit_side = OrderSide.SELL if event.side == PositionSide.LONG else OrderSide.BUY

            self.log.info(
                f"📋 Submitting Layer 0 SL/TP for {event.side.name} position:\n"
                f"   SL: ${sl_price:,.2f} (STOP_MARKET → Algo API)\n"
                f"   TP: ${tp_price:,.2f} (TAKE_PROFIT → Algo API){' [adjusted for fill]' if tp_adjusted else ''}"
            )

            sl_order_id = ""
            tp_order_id = ""
            sl_submitted = False
            tp_submitted = False

            # Submit SL (STOP_MARKET → routed to /fapi/v1/algoOrder by NT 1.222.0)
            try:
                sl_order = self.order_factory.stop_market(
                    instrument_id=self.instrument_id,
                    order_side=exit_side,
                    quantity=self.instrument.make_qty(quantity),
                    trigger_price=self.instrument.make_price(sl_price),
                    trigger_type=TriggerType.LAST_PRICE,
                    reduce_only=True,
                )
                self.submit_order(sl_order)
                sl_submitted = True
                sl_order_id = str(sl_order.client_order_id)

                self.log.info(
                    f"✅ SL order submitted: {exit_side.name} @ ${sl_price:,.2f} "
                    f"(ID: {sl_order_id[:8]}...)"
                )
            except Exception as e:
                self.log.error(f"❌ Failed to submit SL order: {e}")

            # v6.6: Submit TP (LIMIT_IF_TOUCHED → Binance TAKE_PROFIT via /fapi/v1/algoOrder)
            # v36.3: Retry once on failure before giving up
            for tp_attempt in range(2):
                try:
                    tp_order = self.order_factory.limit_if_touched(
                        instrument_id=self.instrument_id,
                        order_side=exit_side,
                        quantity=self.instrument.make_qty(quantity),
                        price=self.instrument.make_price(tp_price),
                        trigger_price=self.instrument.make_price(tp_price),
                        trigger_type=TriggerType.LAST_PRICE,
                        reduce_only=True,
                    )
                    self.submit_order(tp_order)
                    tp_submitted = True
                    tp_order_id = str(tp_order.client_order_id)

                    self.log.info(
                        f"✅ TP order submitted: {exit_side.name} @ ${tp_price:,.2f} "
                        f"(ID: {tp_order_id[:8]}...)"
                    )
                    break  # Success — exit retry loop
                except Exception as e:
                    if tp_attempt == 0:
                        self.log.warning(f"⚠️ TP submission attempt 1 failed: {e}, retrying...")
                    else:
                        self.log.error(f"❌ Failed to submit TP order after 2 attempts: {e}")

            # v7.2: Create layer 0 with order tracking
            confidence = pending.get('confidence', self._position_entry_confidence or 'MEDIUM')
            # v14.1: Only create layer_0 if SL succeeded. When SL fails,
            # _submit_emergency_sl() creates its own layer — creating layer_0 here
            # would cause duplicate layers tracking the same quantity.
            trailing_order_id = ""
            if sl_submitted:
                self._create_layer(
                    entry_price=entry_price,
                    quantity=quantity,
                    side=side_name.lower(),
                    sl_price=sl_price,
                    tp_price=tp_price,
                    sl_order_id=sl_order_id,
                    tp_order_id=tp_order_id,
                    confidence=confidence,
                )

                # v24.2: Submit Binance native TRAILING_STOP_MARKET alongside fixed SL.
                # activation_price = 1.5R profit level — Binance waits server-side until
                # price reaches this level, then starts real-time trailing.
                # No bot polling needed; survives restarts and disconnections.
                # v43.0: Uses 4H ATR for trailing distance (matches SL/TP ATR source).
                risk = abs(entry_price - sl_price)
                if risk > 0:
                    activation_r = getattr(self, '_TRAILING_ACTIVATION_R', 1.5)
                    if side_name == 'LONG':
                        activation_price = entry_price + (risk * activation_r)
                    else:
                        activation_price = entry_price - (risk * activation_r)

                    atr = self._cached_atr_4h or self._cached_atr_value
                    if atr and atr > 0:
                        trailing_atr_mult = getattr(self, '_TRAILING_ATR_MULTIPLIER', 0.6)
                        trailing_distance = atr * trailing_atr_mult
                        trailing_offset_bps = int((trailing_distance / entry_price) * 10000)
                        # Clamp to Binance limits [10, 1000] bps = [0.1%, 10%]
                        trailing_offset_bps = max(10, min(1000, trailing_offset_bps))

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
                            trailing_order_id = str(trailing_order.client_order_id)

                            self.log.info(
                                f"✅ Trailing SL submitted: {exit_side.name} {quantity:.4f} BTC, "
                                f"activation @ ${activation_price:,.2f} (1.1R), "
                                f"callback {trailing_offset_bps / 100:.1f}%"
                            )

                            # Update layer with trailing order info
                            if self._layer_orders:
                                layer_id = next(reversed(self._layer_orders))
                                self._layer_orders[layer_id]['trailing_order_id'] = trailing_order_id
                                self._layer_orders[layer_id]['trailing_activation_price'] = activation_price
                                self._layer_orders[layer_id]['trailing_offset_bps'] = trailing_offset_bps
                                self._order_to_layer[trailing_order_id] = layer_id
                                self._persist_layer_orders()
                        except Exception as e:
                            self.log.warning(
                                f"⚠️ Trailing SL submission failed (non-critical, "
                                f"fixed SL active): {e}"
                            )
                    else:
                        self.log.info(
                            f"ℹ️ Trailing SL skipped: ATR not available "
                            f"(atr={atr}, fixed SL @ ${sl_price:,.2f} still active)"
                        )

            # v5.13: Mark SL/TP as just submitted
            self._sltp_modified_this_cycle = True

            # Safety: If SL failed, submit emergency SL
            if not sl_submitted:
                self.log.error("🚨 SL order failed - submitting emergency SL")
                self._submit_emergency_sl(quantity, event.side.name, reason="SL提交失败")
                # v14.1: Patch emergency layer with real entry data and TP info.
                # _submit_emergency_sl() uses current_price as entry estimate and
                # confidence="EMERGENCY". Update with actual position opening values.
                if self._layer_orders:
                    emg_layer_id = next(iter(self._layer_orders))
                    emg_layer = self._layer_orders[emg_layer_id]
                    emg_layer['entry_price'] = entry_price
                    emg_layer['confidence'] = confidence
                    if tp_submitted and tp_order_id:
                        emg_layer['tp_order_id'] = tp_order_id
                        emg_layer['tp_price'] = tp_price
                        self._order_to_layer[tp_order_id] = emg_layer_id
                    self._persist_layer_orders()

            if not tp_submitted:
                self.log.warning(
                    "⚠️ TP order failed - position has SL protection but no TP. "
                    "Will rely on trailing stop or next signal for exit."
                )

            # v4.16: Send Telegram alert if TP was adjusted for fill price
            if tp_adjusted and tp_submitted:
                if self.telegram_bot and self.enable_telegram:
                    try:
                        original_tp = pending['tp_price']
                        is_long = side_name.upper() == 'LONG'
                        risk = (entry_price - sl_price) if is_long else (sl_price - entry_price)
                        reward = (tp_price - entry_price) if is_long else (entry_price - tp_price)
                        original_reward = (original_tp - entry_price) if is_long else (entry_price - original_tp)
                        original_rr = original_reward / risk if risk > 0 else 0
                        min_rr = get_min_rr_ratio()
                        alert_msg = (
                            f"🔄 *TP 自动调整 (成交后)*\n"
                            f"━━━━━━━━━━━━━━━━━━\n"
                            f"原因: 实际成交价 ${entry_price:,.2f} 导致 R/R 降至 {original_rr:.2f}:1\n"
                            f"旧 TP: ${original_tp:,.2f}\n"
                            f"新 TP: ${tp_price:,.2f}\n"
                            f"R/R: {original_rr:.2f}:1 → {min_rr}:1\n"
                            f"SL: ${sl_price:,.2f} (不变)\n\n"
                            f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
                        )
                        self.telegram_bot.send_message_sync(alert_msg)
                    except Exception as e:
                        self.log.debug(f"Telegram notification failure is non-critical: {e}")
                        pass  # Telegram notification failure is non-critical
        else:
            # No pending SL/TP (e.g. reversal Phase 2)
            if instrument_key in self.sltp_state:
                self._validate_and_adjust_rr_post_fill(
                    instrument_key=instrument_key,
                    fill_price=entry_price,
                    side=event.side.name,
                )

        # v4.0: Send unified trade execution notification (combines signal + fill + position)
        # This replaces 3 separate messages with 1 comprehensive notification
        if self.telegram_bot and self.enable_telegram and self.telegram_notify_positions:
            try:
                # Retrieve SL/TP prices from sltp_state (v3.8)
                instrument_key = str(self.instrument_id)
                sl_price = None
                tp_price = None
                if instrument_key in self.sltp_state:
                    state = self.sltp_state[instrument_key]
                    sl_price = state.get("current_sl_price")
                    tp_price = state.get("current_tp_price")

                # v4.2: Retrieve S/R Zone data (v3.8 fix: extract price_center)
                # v4.15: Include full zone arrays for unified S/R display
                sr_zone_data = None
                nearest_sup_price = None
                nearest_res_price = None
                if self.latest_sr_zones_data:
                    nearest_sup = self.latest_sr_zones_data.get('nearest_support')
                    nearest_res = self.latest_sr_zones_data.get('nearest_resistance')
                    nearest_sup_price = nearest_sup.price_center if nearest_sup else None
                    nearest_res_price = nearest_res.price_center if nearest_res else None

                    def _zone_to_dict(zone):
                        return {
                            'price': zone.price_center,
                            'strength': getattr(zone, 'strength', 'LOW'),
                            'level': getattr(zone, 'level', 'MINOR'),
                        }

                    # v17.0: zones are already 1+1 from calculator, no need to slice
                    support_zones = self.latest_sr_zones_data.get('support_zones', [])
                    resistance_zones = self.latest_sr_zones_data.get('resistance_zones', [])
                    sr_zone_data = {
                        'nearest_support': nearest_sup_price,
                        'nearest_resistance': nearest_res_price,
                        'support_zones': [_zone_to_dict(z) for z in support_zones],
                        'resistance_zones': [_zone_to_dict(z) for z in resistance_zones],
                    }

                # v3.17: Calculate entry quality based on distance from S/R Zone
                entry_quality = None
                entry_price = float(event.avg_px_open)
                side_name = event.side.name

                if side_name == 'LONG' and nearest_sup_price and entry_price > 0:
                    # For LONG: closer to support = better entry
                    dist_pct = ((entry_price - nearest_sup_price) / entry_price) * 100
                    if dist_pct <= 0.5:
                        entry_quality = f"✅ 优秀 (距支撑 {dist_pct:.1f}%)"
                    elif dist_pct <= 1.0:
                        entry_quality = f"✓ 良好 (距支撑 {dist_pct:.1f}%)"
                    elif dist_pct <= 2.0:
                        entry_quality = f"⚠️ 一般 (距支撑 {dist_pct:.1f}%)"
                    else:
                        entry_quality = f"❌ 偏高 (距支撑 {dist_pct:.1f}%)"
                elif side_name == 'SHORT' and nearest_res_price and entry_price > 0:
                    # For SHORT: closer to resistance = better entry
                    dist_pct = ((nearest_res_price - entry_price) / entry_price) * 100
                    if dist_pct <= 0.5:
                        entry_quality = f"✅ 优秀 (距阻力 {dist_pct:.1f}%)"
                    elif dist_pct <= 1.0:
                        entry_quality = f"✓ 良好 (距阻力 {dist_pct:.1f}%)"
                    elif dist_pct <= 2.0:
                        entry_quality = f"⚠️ 一般 (距阻力 {dist_pct:.1f}%)"
                    else:
                        entry_quality = f"❌ 偏低 (距阻力 {dist_pct:.1f}%)"

                # Build unified execution data from pending data + event data
                execution_data = {
                    'side': event.side.name,
                    'quantity': float(event.quantity),
                    'entry_price': entry_price,
                    'sl_price': sl_price,
                    'tp_price': tp_price,
                    'sr_zone': sr_zone_data,  # v4.2: Add S/R Zone
                    'entry_quality': entry_quality,  # v3.17: Entry quality evaluation
                }

                # Merge with pending execution data (signal info, technical indicators, AI analysis)
                if self._pending_execution_data:
                    execution_data.update({
                        'signal': self._pending_execution_data.get('signal', 'BUY' if event.side.name == 'LONG' else 'SELL'),
                        'confidence': self._pending_execution_data.get('confidence', 'MEDIUM'),
                        'rsi': self._pending_execution_data.get('rsi'),
                        'macd': self._pending_execution_data.get('macd'),
                        'winning_side': self._pending_execution_data.get('winning_side', ''),
                        'reasoning': self._pending_execution_data.get('reasoning', ''),
                        'judge_rationale': self._pending_execution_data.get('judge_rationale', ''),
                        'risk_level': self._pending_execution_data.get('risk_level', 'MEDIUM'),
                        'position_size_pct': self._pending_execution_data.get('position_size_pct'),
                        # v5.7: Pass confluence for Telegram display
                        'confluence': self._pending_execution_data.get('confluence', {}),
                        # v23.0: Entry Timing assessment for Telegram display
                        'timing_verdict': self._pending_execution_data.get('timing_verdict', ''),
                        'timing_quality': self._pending_execution_data.get('timing_quality', ''),
                        # v19.2: Flow signals for Telegram display
                        'flow_signals': self._pending_execution_data.get('flow_signals', {}),
                        # v29.0: AI quality score and calibration for Telegram display
                        'quality_score': self._pending_execution_data.get('quality_score'),
                        'calibration': self._pending_execution_data.get('calibration'),
                    })
                    # Clear pending data after use
                    self._pending_execution_data = None
                else:
                    # Fallback if no pending data (shouldn't happen normally)
                    execution_data['signal'] = 'BUY' if event.side.name == 'LONG' else 'SELL'
                    execution_data['confidence'] = 'MEDIUM'

                # Send unified message
                execution_msg = self.telegram_bot.format_trade_execution(execution_data)
                self.telegram_bot.send_message_sync(execution_msg, broadcast=True)
                self.log.info("✅ Sent unified trade execution notification")
            except Exception as e:
                self.log.warning(f"Failed to send Telegram trade execution notification: {e}")

    def on_position_closed(self, event):
        """Handle position closed events."""
        # v13.0: Reset manual close flag — position closed normally via NT event
        with self._state_lock:
            self._manual_close_in_progress = False
        # v18.0: Reset emergency retry state — position closed, no more retries needed
        self._emergency_retry_count = 0
        self._needs_emergency_review = False
        # v18.3: Reset signal fingerprint — allow same direction re-entry after close.
        # Without this, fingerprint dedup blocks identical signals indefinitely
        # (e.g., SHORT|HIGH before close → SHORT|HIGH after close = "重复信号" forever).
        self._last_executed_fingerprint = ""

        # v18.3: Force 2 extra analysis cycles after close.
        # The first post-close cycle triggers naturally via Check 3 (position_state_changed),
        # this counter provides 2 additional forced cycles = total 3 consecutive analyses (~45 min).
        # Gives AI multiple chances to evaluate re-entry instead of entering skip-gate immediately.
        # Safe with stoploss cooldown: cooldown check runs BEFORE _has_market_changed(),
        # so counter is only consumed after cooldown expires.
        self._force_analysis_cycles_remaining = 2

        # v35.0: Determine actual position direction from opening order side.
        # CRITICAL: event.side returns PositionSide.FLAT after close (NT asserts this),
        # NOT the original position direction. Using event.side.name ('FLAT') causes:
        #   - side_to_cn('FLAT') → always '空' (all closes show as SHORT)
        #   - close_reason detection skips both LONG/SHORT branches (never detects SL/TP)
        # Fix: use event.entry (opening OrderSide: BUY=LONG, SELL=SHORT).
        # event.entry is always available on PositionEvent (NT 1.222.0 Cython source confirms).
        _position_side_name = 'LONG' if event.entry == OrderSide.BUY else 'SHORT'

        self.log.info(
            f"🔴 Position closed: {_position_side_name} "
            f"P&L: {float(event.realized_pnl):.2f} USDT"
        )
        
        # Capture SL/TP before clearing state (needed for trade evaluation)
        # Note: on_order_filled fires BEFORE on_position_closed. When the last layer's
        # SL/TP fills, _remove_layer() → _update_aggregate_sltp_state() already clears
        # sltp_state. Use _pre_close_sltp_snapshot as fallback.
        instrument_key = str(self.instrument_id)
        captured_sltp = None
        if instrument_key in self.sltp_state:
            captured_sltp = self.sltp_state[instrument_key].copy()
            del self.sltp_state[instrument_key]
            self.log.debug(f"🗑️ Cleared trailing stop state for {instrument_key}")
        elif self._pre_close_sltp_snapshot:
            captured_sltp = self._pre_close_sltp_snapshot
            self.log.debug(f"📸 Using pre-close SLTP snapshot (cleared by on_order_filled)")

        # v7.2: Capture per-layer data for evaluation, then clear
        # Same timing issue: _remove_layer already cleared _layer_orders.
        captured_layers = dict(self._layer_orders)
        if not captured_layers and self._pre_close_last_layer:
            captured_layers = {'_snapshot': self._pre_close_last_layer}
            self.log.debug(f"📸 Using pre-close last layer snapshot")
        self._layer_orders.clear()
        self._order_to_layer.clear()
        self._next_layer_idx = 0  # Reset monotonic counter on position close
        self._ghost_first_seen = 0.0  # v36.3: Clear ghost flag on position close
        self._dca_virtual_avg = 0  # v48.0: Clear virtual DCA state
        self._dca_virtual_last_price = 0
        self._persist_layer_orders()

        # v15.4: Safety net — cancel ALL remaining reduce_only orders on exchange.
        # Prevents conditional order accumulation from:
        #   - SL replacement with failed cancel (old SL orphaned)
        #   - on_order_filled peer cancel failure (peer order orphaned)
        #   - NT cache vs Binance Algo API desync (order invisible to NT)
        # Must run BEFORE reversal Phase 2 (reversal entry is NOT reduce_only, safe).
        try:
            # Phase 1: Cancel orders visible to NT cache
            open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
            remaining_reduce_only = [o for o in open_orders if o.is_reduce_only]
            if remaining_reduce_only:
                self.log.warning(
                    f"🧹 Position closed but {len(remaining_reduce_only)} reduce_only orders "
                    f"still in NT cache — cancelling all"
                )
                for order in remaining_reduce_only:
                    try:
                        self.cancel_order(order)
                    except Exception as e:
                        self.log.debug(f"NT orphan cancel failed (will retry via Binance API): {e}")

            # Phase 2: Cancel orders on Binance not visible to NT cache (Algo API orders)
            if self.binance_account:
                try:
                    symbol = str(self.instrument_id).replace('-PERP', '').replace('.BINANCE', '').upper()
                    cleanup = self.binance_account.cancel_all_open_orders(symbol)
                    cancelled = cleanup.get('regular_cancelled', 0)
                    algo_cancelled = cleanup.get('algo_cancelled', 0)
                    if cancelled or algo_cancelled:
                        self.log.warning(
                            f"🧹 Binance direct cleanup: {cancelled} regular + "
                            f"{algo_cancelled} Algo orders cancelled"
                        )
                    errors = cleanup.get('errors', [])
                    if errors:
                        self.log.warning(f"⚠️ Binance cleanup errors: {errors}")
                except Exception as e:
                    self.log.warning(f"⚠️ Binance direct cleanup failed (non-critical): {e}")
        except Exception as e:
            self.log.warning(f"⚠️ Post-close order cleanup failed: {e}")

        # Store for trade evaluation later
        self._captured_sltp_for_eval = captured_sltp
        self._captured_layers_for_eval = captured_layers

        # Clear snapshots after consumption (prevent stale data in future closures)
        self._pre_close_sltp_snapshot = None
        self._pre_close_last_layer = None

        # v3.18: Check for pending reversal (Phase 2 of two-phase commit)
        # If we have a pending reversal, open the new position now that old one is closed
        # Note: evaluation + record_outcome runs AFTER this block (no early return)
        is_reversal = False
        if hasattr(self, '_pending_reversal') and self._pending_reversal:
            is_reversal = True
            pending = self._pending_reversal
            self._pending_reversal = None  # Clear state immediately to prevent double execution

            # v6.0: Timeout safety — abort if Phase 2 triggered >5min after Phase 1
            submitted_at = pending.get('submitted_at')
            if submitted_at:
                elapsed = (datetime.now(timezone.utc) - submitted_at).total_seconds()
                if elapsed > self.reversal_timeout_seconds:
                    self.log.error(
                        f"❌ Reversal Phase 2 TIMEOUT: {elapsed:.0f}s since Phase 1 "
                        f"(max {self.reversal_timeout_seconds}s). Market conditions may have changed. Aborting reversal."
                    )
                    if self.telegram_bot and self.enable_telegram:
                        try:
                            self.telegram_bot.send_message_sync(
                                f"🚨 反转超时取消\n\n"
                                f"Phase 1 → Phase 2 等待了 {elapsed:.0f}秒 (>5分钟)\n"
                                f"市场条件可能已变化，取消反转以避免错误入场\n\n"
                                f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
                            )
                        except Exception as e:
                            self.log.debug(f"Telegram notification failure is non-critical: {e}")
                            pass  # Telegram notification failure is non-critical
                    # Skip reversal Phase 2 — let next on_timer decide fresh
                    pending = None

            # Only proceed if not timed out
            if pending:
                target_side = pending['target_side']
                target_quantity = pending['target_quantity']
                old_side = pending['old_side']
                submitted_at = pending.get('submitted_at', datetime.now(timezone.utc))

                # Calculate time elapsed since reversal was initiated
                elapsed = (datetime.now(timezone.utc) - submitted_at).total_seconds()

                old_side_cn = '多' if old_side == 'long' else '空'
                new_side_cn = '多' if target_side == 'long' else '空'

                self.log.info(
                    f"🔄 Reversal Phase 2: Old position closed, opening new {target_side} position "
                    f"(elapsed: {elapsed:.1f}s)"
                )

                # Verify no position exists before opening new one (safety check)
                current_pos = self._get_current_position_data()
                if current_pos:
                    self.log.error(
                        f"❌ Reversal Phase 2 aborted: Position still exists after close event! "
                        f"Side: {current_pos['side']}, Qty: {current_pos['quantity']:.4f}"
                    )
                    # Send alert
                    if self.telegram_bot and self.enable_telegram:
                        try:
                            alert_msg = self.telegram_bot.format_error_alert({
                                'level': 'CRITICAL',
                                'message': f"反转失败：平仓后仍有持仓",
                                'context': f"预期: 无持仓, 实际: {current_pos['side']} {current_pos['quantity']:.4f}",
                            })
                            self.telegram_bot.send_message_sync(alert_msg)
                        except Exception as e:
                            self.log.debug(f"Telegram notification failure is non-critical: {e}")
                            pass  # Telegram notification failure is non-critical
                    return

                # Open new position with bracket order
                new_order_side = OrderSide.BUY if target_side == 'long' else OrderSide.SELL
                try:
                    self._submit_bracket_order(
                        side=new_order_side,
                        quantity=target_quantity,
                    )
                    self.log.info(
                        f"✅ Reversal Phase 2 complete: Opened {target_side} {target_quantity:.4f} BTC "
                        f"(with bracket SL/TP)"
                    )

                    # Update signal status
                    self._last_signal_status = {
                        'executed': True,
                        'reason': '',
                        'action_taken': f'反转完成: {old_side_cn}→{new_side_cn}',
                    }

                    # Send Telegram notification for successful reversal
                    if self.telegram_bot and self.enable_telegram:
                        try:
                            with self._state_lock:
                                _r_price = self._cached_current_price or 0
                            _r_value = target_quantity * _r_price if _r_price > 0 else 0
                            _r_qty_str = f"{target_quantity:.4f} BTC (${_r_value:,.0f})" if _r_value > 0 else f"{target_quantity:.4f} BTC"
                            reversal_msg = (
                                f"🔄 *反转成功*\n\n"
                                f"*方向*: {old_side_cn} → {new_side_cn}\n"
                                f"*数量*: {_r_qty_str}\n"
                                f"*耗时*: {elapsed:.1f}秒\n\n"
                                f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
                            )
                            self.telegram_bot.send_message_sync(reversal_msg)
                        except Exception as e:
                            self.log.warning(f"Failed to send reversal notification: {e}")

                except Exception as e:
                    self.log.error(f"❌ Reversal Phase 2 failed: {e}")
                    self._last_signal_status = {
                        'executed': False,
                        'reason': f'反转开仓失败: {str(e)}',
                        'action_taken': '',
                    }

                    # Send critical alert
                    if self.telegram_bot and self.enable_telegram:
                        try:
                            alert_msg = self.telegram_bot.format_error_alert({
                                'level': 'CRITICAL',
                                'message': f"反转Phase 2失败：无法开新仓",
                                'context': f"目标: {target_side} {target_quantity:.4f}, 错误: {str(e)}",
                            })
                            self.telegram_bot.send_message_sync(alert_msg)
                        except Exception as e:
                            self.log.debug(f"Telegram notification failure is non-critical: {e}")
                            pass  # Telegram notification failure is non-critical

            # Don't return early — fall through to P&L computation + evaluation + record_outcome
            # so that reversal trades are also recorded for AI learning (Bug #2 fix).
            # v14.1: Reversal close P&L is now broadcast to subscribers (was previously skipped).

        # v3.12: Calculate P&L percentage upfront (needed for both Telegram and memory system)
        # v3.13: Fix - NautilusTrader uses Money/Quantity types, need .as_double()
        # realized_pnl is Money type, quantity is Quantity type
        try:
            # Money type has .as_double() method
            pnl = event.realized_pnl.as_double() if hasattr(event.realized_pnl, 'as_double') else float(event.realized_pnl)
        except (AttributeError, TypeError, ValueError):
            pnl = 0.0
            self.log.warning(f"Failed to extract realized_pnl from event: {type(event.realized_pnl)}")

        # v24.1: Fix quantity extraction for PositionClosed event.
        # When a position is CLOSED, event.quantity = 0 (FLAT state).
        # We need peak_qty (max position size) or last fill qty instead.
        quantity = 0.0
        qty_source = "none"
        try:
            # Priority 1: peak_qty — maximum position size during lifetime
            # This is the most meaningful "position size" for close messages
            if hasattr(event, 'peak_qty') and event.peak_qty is not None:
                qty_obj = event.peak_qty
                quantity = qty_obj.as_double() if hasattr(qty_obj, 'as_double') else float(qty_obj)
                if quantity > 0:
                    qty_source = "peak_qty"

            # Priority 2: last fill quantity (from last OrderFilled event)
            if quantity == 0 and hasattr(event, 'last_event') and event.last_event is not None:
                last_fill = event.last_event
                if hasattr(last_fill, 'last_qty') and last_fill.last_qty is not None:
                    qty_obj = last_fill.last_qty
                    quantity = qty_obj.as_double() if hasattr(qty_obj, 'as_double') else float(qty_obj)
                    if quantity > 0:
                        qty_source = "last_fill_qty"

            # Priority 3: Calculate from realized_pnl and price difference
            if quantity == 0 and pnl != 0:
                _entry = float(event.avg_px_open) if hasattr(event, 'avg_px_open') else 0.0
                _exit = float(event.avg_px_close) if hasattr(event, 'avg_px_close') else 0.0
                if _entry > 0 and _exit > 0 and abs(_exit - _entry) > 0:
                    quantity = abs(pnl / (_exit - _entry))
                    if quantity > 0:
                        qty_source = "calculated_from_pnl"

            # Priority 4: signed_qty (non-zero only for open positions, but try anyway)
            if quantity == 0 and hasattr(event, 'signed_qty') and event.signed_qty is not None:
                try:
                    sq = event.signed_qty
                    sq_val = sq.as_double() if hasattr(sq, 'as_double') else float(sq)
                    if abs(sq_val) > 0:
                        quantity = abs(sq_val)
                        qty_source = "signed_qty"
                except (AttributeError, TypeError, ValueError):
                    self.log.debug("signed_qty extraction failed, trying next priority")
                    pass

            # Priority 5: quantity (will be 0 for closed position, but keep as last resort)
            if quantity == 0 and hasattr(event, 'quantity') and event.quantity is not None:
                qty_obj = event.quantity
                q = qty_obj.as_double() if hasattr(qty_obj, 'as_double') else float(qty_obj)
                if q > 0:
                    quantity = q
                    qty_source = "quantity"
        except (AttributeError, TypeError, ValueError) as e:
            self.log.warning(f"Failed to extract quantity from event: {e}")
            quantity = 0.0

        # avg_px_open and avg_px_close are plain doubles in PositionClosed event
        entry_price = float(event.avg_px_open) if hasattr(event, 'avg_px_open') else 0.0
        exit_price = float(event.avg_px_close) if hasattr(event, 'avg_px_close') else 0.0
        position_value = entry_price * quantity

        # v3.16: Use official realized_return attribute (ROOT CAUSE FIX)
        # NautilusTrader PositionClosed event has realized_return (decimal, e.g., 0.0123 = 1.23%)
        # This is the authoritative source, avoiding manual calculation issues
        pnl_pct = 0.0
        pnl_source = "none"

        # Priority 1: Use official realized_return from NautilusTrader
        if hasattr(event, 'realized_return'):
            try:
                # realized_return is a decimal (0.01 = 1%), convert to percentage
                pnl_pct = float(event.realized_return) * 100
                pnl_source = "realized_return"
            except (TypeError, ValueError) as e:
                self.log.warning(f"Failed to extract realized_return: {e}")

        # Priority 2: Calculate from pnl/position_value
        if pnl_pct == 0.0 and position_value > 0:
            pnl_pct = (pnl / position_value * 100)
            pnl_source = "pnl/position_value"

        # Priority 3: Calculate from price difference
        if pnl_pct == 0.0 and pnl != 0 and entry_price > 0 and exit_price > 0:
            side = _position_side_name
            if side == 'LONG':
                pnl_pct = ((exit_price - entry_price) / entry_price) * 100
            elif side == 'SHORT':
                pnl_pct = ((entry_price - exit_price) / entry_price) * 100
            pnl_source = "price_difference"

        # v24.1: Cross-validate PnL sign against position direction and price movement.
        # In ghost position scenarios, realized_return from NT may have wrong sign.
        if pnl_pct != 0.0 and entry_price > 0 and exit_price > 0:
            _side = _position_side_name
            price_went_up = exit_price > entry_price
            if _side == 'LONG':
                # LONG profits when price goes up
                expected_positive = price_went_up
            elif _side == 'SHORT':
                # SHORT profits when price goes down
                expected_positive = not price_went_up
            else:
                expected_positive = None

            if expected_positive is not None:
                if expected_positive and pnl_pct < 0:
                    self.log.warning(
                        f"⚠️ PnL sign mismatch: {_side} exit=${exit_price:,.2f} "
                        f"{'>' if price_went_up else '<'} entry=${entry_price:,.2f} "
                        f"but pnl_pct={pnl_pct:.2f}% (negative). Correcting sign."
                    )
                    pnl_pct = abs(pnl_pct)
                    pnl = abs(pnl)
                elif not expected_positive and pnl_pct > 0:
                    self.log.warning(
                        f"⚠️ PnL sign mismatch: {_side} exit=${exit_price:,.2f} "
                        f"{'>' if price_went_up else '<'} entry=${entry_price:,.2f} "
                        f"but pnl_pct={pnl_pct:.2f}% (positive). Correcting sign."
                    )
                    pnl_pct = -abs(pnl_pct)
                    pnl = -abs(pnl)

        # v3.16: Enhanced debug logging with source tracking
        self.log.info(
            f"📊 P&L calculation: pnl={pnl:.4f} USDT, qty={quantity:.4f} (from {qty_source}), "
            f"entry={entry_price:.2f}, exit={exit_price:.2f}, pnl_pct={pnl_pct:.2f}% (from {pnl_source})"
        )

        # v3.12: Record trade in RiskController for circuit breaker tracking
        try:
            side_str = _position_side_name
            if entry_price > 0 and quantity > 0:
                self.risk_controller.record_trade_simple(
                    side=side_str,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    quantity=quantity,
                )
                risk_state = self.risk_controller.metrics.trading_state
                if risk_state != TradingState.ACTIVE:
                    self.log.warning(
                        f"🚨 Risk state after trade: {risk_state.value} - "
                        f"{self.risk_controller.metrics.halt_reason}"
                    )
        except Exception as e:
            self.log.warning(f"Failed to record trade in RiskController: {e}")

        # v2.0: Record trade in Prometheus metrics
        _prom = getattr(self, '_metrics_exporter', None)
        if _prom:
            try:
                _pnl_pct = (pnl / (entry_price * quantity)) * 100 if entry_price > 0 and quantity > 0 else 0
                _conf = getattr(self, '_last_position_confidence', 'MEDIUM') or 'MEDIUM'
                _prom.record_trade(side=_position_side_name, pnl_pct=_pnl_pct, confidence=_conf)
            except Exception as e:
                self.log.debug(f"Prometheus trade record failed: {e}")

        # v6.0: Activate per-stop cooldown only for actual stop-loss exits
        # Detect SL exit by comparing exit_price to tracked current_sl_price
        # Note: sltp_state may already be cleared by on_order_filled → use captured_sltp as fallback
        if pnl < 0 and self.cooldown_enabled:
            tracked_sl = (captured_sltp or {}).get('current_sl_price')
            is_stoploss_exit = False
            if tracked_sl and tracked_sl > 0 and exit_price > 0:
                # SL exit: exit_price within 0.5% of tracked SL (accounts for slippage)
                sl_tolerance = tracked_sl * 0.005
                is_stoploss_exit = abs(exit_price - tracked_sl) <= sl_tolerance

            if is_stoploss_exit:
                self._activate_stoploss_cooldown(
                    exit_price=exit_price,
                    entry_price=entry_price,
                    side=_position_side_name,
                )
            else:
                self.log.info(
                    f"ℹ️ Loss exit (pnl={pnl:.2f}) was NOT a stop-loss hit "
                    f"(exit=${exit_price:,.2f} vs tracked_sl=${tracked_sl or 0:,.2f}) — "
                    f"cooldown not activated"
                )

        # Capture values needed by evaluate_trade() BEFORE clearing.
        # These fields are read 200+ lines later in evaluate_trade — clearing first
        # causes them to always be 0/None/"" (clear-too-early-read-too-late bug).
        _captured_pyramid_layers = len(self._position_layers) if self._position_layers else 1
        _captured_confidence_at_exit = self._last_position_confidence or ""
        _captured_mae_entry = self._position_entry_price_for_mae
        _captured_mae_max = self._position_max_price
        _captured_mae_min = self._position_min_price
        _captured_mae_is_long = self._position_is_long_for_mae
        _captured_atr_value = self._entry_atr_value
        _captured_sl_atr_mult = self._entry_sl_atr_multiplier
        _captured_is_counter_trend = self._entry_is_counter_trend
        _captured_risk_appetite = self._entry_risk_appetite
        _captured_trend_direction = self._entry_trend_direction
        _captured_adx = self._entry_adx

        # v6.0: Clear pyramiding and confidence state on position close
        self._position_layers = []
        self._save_position_layers()
        self._position_entry_confidence = None
        self._last_position_confidence = None
        # v15.0 P2: Clear confidence decay tracking on position close
        self._confidence_history = []
        self._decay_warned_levels = set()

        # v11.5: Reset MAE/MFE and entry-time tracking
        self._position_entry_price_for_mae = 0.0
        self._position_max_price = 0.0
        self._position_min_price = float('inf')
        self._entry_atr_value = 0.0
        self._entry_sl_atr_multiplier = 0.0
        self._entry_is_counter_trend = False
        self._entry_risk_appetite = ""
        self._entry_trend_direction = ""
        self._entry_adx = 0.0

        # v6.6: SL (STOP_MARKET) and TP (TAKE_PROFIT via LIMIT_IF_TOUCHED) both go through
        # Binance Algo API and are position-linked (GTE_GTC). Binance auto-cancels them
        # when the position closes. This cleanup is defense-in-depth only — it handles
        # edge cases where NT's cache shows stale orders that Binance already cancelled.
        try:
            open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
            orphan_count = 0
            for order in open_orders:
                if order.is_reduce_only:
                    try:
                        self.cancel_order(order)
                        orphan_count += 1
                    except Exception as e:
                        self.log.debug(f"Orphan cancel skipped (likely already cancelled by Binance): {e}")
            if orphan_count > 0:
                self.log.info(
                    f"🗑️ Defence-in-depth: cancelled {orphan_count} orphan order(s) from NT cache"
                )
        except Exception as e:
            self.log.debug(f"Orphan cleanup skipped: {e}")

        # v14.1: Reversal close also broadcasts P&L to subscribers (was previously skipped).
        # Subscribers see: old position close P&L → new position entry signal.
        if is_reversal and not self._forced_close_reason:
            # Set forced close reason for reversal (consumed in close_reason detection below)
            old_side_cn = '多' if _position_side_name in ('BUY', 'LONG') else '空'
            new_side_cn = '空' if _position_side_name in ('BUY', 'LONG') else '多'
            self._forced_close_reason = (
                'REVERSAL',
                f'🔄 反转平仓 ({old_side_cn} → {new_side_cn})',
            )

        # Send Telegram position closed notification
        if self.telegram_bot and self.enable_telegram and self.telegram_notify_positions:
            try:
                # v4.2: Retrieve S/R Zone data for close message (v3.8 fix: extract price_center)
                sr_zone_data = None
                if self.latest_sr_zones_data:
                    nearest_sup = self.latest_sr_zones_data.get('nearest_support')
                    nearest_res = self.latest_sr_zones_data.get('nearest_resistance')
                    sr_zone_data = {
                        'nearest_support': nearest_sup.price_center if nearest_sup else None,
                        'nearest_resistance': nearest_res.price_center if nearest_res else None,
                    }

                # v3.17: Determine close reason (SL/TP/Manual)
                # v14.1: Check for forced close reason first (time barrier, emergency)
                close_reason = 'MANUAL'  # Default
                close_reason_detail = '手动平仓'

                if self._forced_close_reason:
                    close_reason, close_reason_detail = self._forced_close_reason
                    self._forced_close_reason = None
                else:
                    # Get SL/TP prices for close reason detection
                    # Priority 1: captured_sltp (authoritative — actual SL/TP on exchange)
                    # Priority 2: latest_signal_data (may be stale if HOLD cycle overwrote it)
                    sl_price = None
                    tp_price = None

                    # v6.1 fix: Use captured_sltp (saved at line 6980 before sltp_state cleared)
                    # This survives HOLD signal overwriting latest_signal_data
                    captured = getattr(self, '_captured_sltp_for_eval', None)
                    if captured:
                        sl_price = captured.get('current_sl_price')
                        tp_price = captured.get('current_tp_price')

                    # Priority 2: captured_layers — layer data has per-layer sl_price/tp_price
                    if not sl_price:
                        captured_layers = getattr(self, '_captured_layers_for_eval', None)
                        if captured_layers:
                            for lid, ldata in captured_layers.items():
                                layer_sl = ldata.get('sl_price', 0)
                                layer_tp = ldata.get('tp_price', 0)
                                if layer_sl and layer_sl > 0:
                                    sl_price = layer_sl
                                    tp_price = layer_tp if layer_tp and layer_tp > 0 else None
                                    break

                    # Priority 3: latest_signal_data (only if captured_sltp didn't have values)
                    if not sl_price and hasattr(self, 'latest_signal_data') and self.latest_signal_data:
                        sl_price = self.latest_signal_data.get('stop_loss')
                    if not tp_price and hasattr(self, 'latest_signal_data') and self.latest_signal_data:
                        tp_price = self.latest_signal_data.get('take_profit')

                    if sl_price and tp_price and exit_price > 0:
                        side = _position_side_name
                        sl_tolerance = entry_price * 0.002  # 0.2% tolerance
                        tp_tolerance = entry_price * 0.002

                        # v24.1: Direction validation — reject stale/swapped SL/TP
                        # LONG: SL < entry < TP; SHORT: TP < entry < SL
                        sl_tp_valid = True
                        if side == 'LONG' and not (sl_price < entry_price < tp_price):
                            self.log.warning(
                                f"⚠️ SL/TP direction invalid for LONG: "
                                f"SL=${sl_price:,.2f} entry=${entry_price:,.2f} TP=${tp_price:,.2f} — "
                                f"defaulting to MANUAL close reason"
                            )
                            sl_tp_valid = False
                        elif side == 'SHORT' and not (tp_price < entry_price < sl_price):
                            self.log.warning(
                                f"⚠️ SL/TP direction invalid for SHORT: "
                                f"TP=${tp_price:,.2f} entry=${entry_price:,.2f} SL=${sl_price:,.2f} — "
                                f"defaulting to MANUAL close reason"
                            )
                            sl_tp_valid = False

                        if sl_tp_valid and side == 'LONG':
                            if exit_price <= sl_price + sl_tolerance:
                                close_reason = 'STOP_LOSS'
                                close_reason_detail = f'🛑 止损触发 (SL: ${sl_price:,.2f})'
                            elif exit_price >= tp_price - tp_tolerance:
                                close_reason = 'TAKE_PROFIT'
                                close_reason_detail = f'🎯 止盈触发 (TP: ${tp_price:,.2f})'
                        elif sl_tp_valid and side == 'SHORT':
                            if exit_price >= sl_price - sl_tolerance:
                                close_reason = 'STOP_LOSS'
                                close_reason_detail = f'🛑 止损触发 (SL: ${sl_price:,.2f})'
                            elif exit_price <= tp_price + tp_tolerance:
                                close_reason = 'TAKE_PROFIT'
                                close_reason_detail = f'🎯 止盈触发 (TP: ${tp_price:,.2f})'

                self.log.info(f"📊 Close reason: {close_reason} - {close_reason_detail}")

                # v10.0: Mechanical mode direction lock — track consecutive SL per direction
                if close_reason == 'STOP_LOSS' and getattr(self, '_strategy_mode', 'ai') == 'mechanical':
                    try:
                        from agents.mechanical_decide import record_stoploss
                        record_stoploss(_position_side_name)
                        self.log.info(f"📊 Direction lock: recorded SL for {_position_side_name}")
                    except Exception as e:
                        self.log.debug(f"Direction lock record_stoploss: {e}")

                position_msg = self.telegram_bot.format_position_update({
                    'action': 'CLOSED',
                    'side': _position_side_name,
                    'quantity': quantity,
                    'entry_price': entry_price,
                    'current_price': exit_price,
                    'pnl': pnl,
                    'pnl_pct': pnl_pct,
                    'sr_zone': sr_zone_data,  # v4.2: Add S/R Zone
                    'close_reason': close_reason,  # v3.17: SL/TP/MANUAL
                    'close_reason_detail': close_reason_detail,  # v3.17: Human-readable
                })

                # v35.0: Cross-validate against Binance before broadcasting close notification.
                # After server restart, NT reconciliation can fire on_position_closed for
                # stale/phantom positions, sending false notifications to the subscriber
                # channel while the actual position remains open on Binance.
                skip_broadcast = False
                try:
                    if self.binance_account:
                        symbol = str(self.instrument_id).replace('-PERP', '').replace('.BINANCE', '').upper()
                        binance_pos = self.binance_account.get_positions(symbol)
                        if binance_pos and len(binance_pos) > 0:
                            binance_amt = float(binance_pos[0].get('positionAmt', 0))
                            if abs(binance_amt) > 0:
                                binance_side = 'LONG' if binance_amt > 0 else 'SHORT'
                                event_side = _position_side_name.upper()
                                # Same-side still open → false close event (skip broadcast).
                                # Different-side open → reversal completed (broadcast is valid).
                                if (event_side in ('LONG', 'BUY') and binance_side == 'LONG') or \
                                   (event_side in ('SHORT', 'SELL') and binance_side == 'SHORT'):
                                    skip_broadcast = True
                                    self.log.error(
                                        f"🚨 FALSE CLOSE: on_position_closed fired for {event_side} "
                                        f"but Binance still has {binance_side} {abs(binance_amt):.4f} BTC! "
                                        f"Skipping broadcast to prevent false alert."
                                    )
                except Exception as e:
                    self.log.warning(f"⚠️ Binance cross-validation failed during close notification: {e}")

                if not skip_broadcast:
                    self.telegram_bot.send_message_sync(position_msg, broadcast=True)
                else:
                    # Send to private chat only for debugging, NOT to subscriber channel
                    self.telegram_bot.send_message_sync(
                        f"⚠️ *疑似误报* (Binance 仍有同方向持仓)\n\n{position_msg}",
                        broadcast=False,
                    )
            except Exception as e:
                self.log.warning(f"Failed to send Telegram position closed notification: {e}")

        # v3.12: Record outcome for AI learning
        # v5.1: Enhanced with trade evaluation (grade, R/R, execution quality)
        # v5.12: Separate exception handlers for evaluate vs record; log ERROR on failure
        try:
            if hasattr(self, 'multi_agent') and self.multi_agent:
                # v6.0 fix: Use actual position direction from event, NOT self.last_signal
                # self.last_signal gets overwritten every on_timer cycle, so by the time
                # on_position_closed fires, it may show HOLD/CLOSE instead of the
                # original entry direction (LONG/SHORT). This corrupts AI learning memory.
                decision = "UNKNOWN"
                confidence = "MEDIUM"
                position_size_pct = 0.0

                # Priority 1: Position direction from event (authoritative)
                # v35.0: Use _position_side_name (derived from event.entry)
                # instead of event.side.name which returns 'FLAT' on close.
                if _position_side_name in ('LONG', 'BUY'):
                    decision = 'LONG'
                elif _position_side_name in ('SHORT', 'SELL'):
                    decision = 'SHORT'

                # Priority 2: Fallback to last_signal (only if event had no side)
                if decision == "UNKNOWN" and hasattr(self, 'last_signal') and self.last_signal:
                    signal = self.last_signal.get('signal', '')
                    legacy_mapping = {'BUY': 'LONG', 'SELL': 'SHORT'}
                    decision = legacy_mapping.get(signal, signal)

                if hasattr(self, 'last_signal') and self.last_signal:
                    confidence = self.last_signal.get('confidence', 'MEDIUM')
                    position_size_pct = self.last_signal.get('position_size_pct', 0) or 0

                # Get entry conditions
                conditions = getattr(self, '_last_entry_conditions', 'N/A')

                # v5.1: Compute trade evaluation
                evaluation = None
                eval_error_reason = None
                try:
                    # Get planned SL/TP from sltp_state or latest_signal_data
                    planned_sl = None
                    planned_tp = None
                    instrument_key = str(self.instrument_id)

                    # Priority 1: captured_sltp — prefer validated (original) over current (may be emergency SL)
                    state = getattr(self, '_captured_sltp_for_eval', None)
                    if state:
                        planned_sl = state.get('validated_sl_price') or state.get('current_sl_price')
                        planned_tp = state.get('validated_tp_price') or state.get('current_tp_price')

                    # Priority 2: captured_layers — layer data has per-layer sl_price/tp_price
                    # This covers the case where on_order_filled clears sltp_state
                    # (Algo API ID mismatch prevents _remove_layer → _pre_close_sltp_snapshot),
                    # but layer data was captured before clearing in on_position_closed.
                    sltp_source = "captured_sltp" if planned_sl else "none"
                    if not planned_sl:
                        layers = getattr(self, '_captured_layers_for_eval', None)
                        if layers:
                            # Use the first (or only) layer's SL/TP
                            for lid, ldata in layers.items():
                                layer_sl = ldata.get('sl_price', 0)
                                layer_tp = ldata.get('tp_price', 0)
                                if layer_sl and layer_sl > 0:
                                    planned_sl = layer_sl
                                    planned_tp = layer_tp if layer_tp and layer_tp > 0 else None
                                    sltp_source = "captured_layers"
                                    break

                    # Priority 3: latest_signal_data (AI planned values — may be stale!)
                    if not planned_sl and hasattr(self, 'latest_signal_data') and self.latest_signal_data:
                        planned_sl = self.latest_signal_data.get('stop_loss')
                        planned_tp = self.latest_signal_data.get('take_profit')
                        sltp_source = "latest_signal_data (fallback)"

                    # v6.0: Clear after use to prevent stale data in future evaluations
                    self._captured_sltp_for_eval = None
                    self._captured_layers_for_eval = None

                    # v6.0: Direction-aware validation of captured SL/TP
                    # If TP is on wrong side of entry (due to averaged entry or stale data),
                    # invalidate TP to prevent nonsensical planned_rr in memory
                    if planned_sl and planned_tp and entry_price > 0:
                        direction_check = _position_side_name
                        is_long_check = direction_check in ('LONG', 'BUY')
                        if is_long_check:
                            if planned_tp <= entry_price:
                                self.log.warning(
                                    f"⚠️ Stale TP detected: LONG but TP=${planned_tp:,.2f} <= entry=${entry_price:,.2f} "
                                    f"(source: {sltp_source}) — invalidating TP for evaluation"
                                )
                                planned_tp = None
                        elif direction_check in ('SHORT', 'SELL'):
                            if planned_tp >= entry_price:
                                self.log.warning(
                                    f"⚠️ Stale TP detected: SHORT but TP=${planned_tp:,.2f} >= entry=${entry_price:,.2f} "
                                    f"(source: {sltp_source}) — invalidating TP for evaluation"
                                )
                                planned_tp = None

                    # v5.12: Log when planned_sl/tp are missing (affects grade accuracy)
                    if not planned_sl or not planned_tp:
                        self.log.warning(
                            f"⚠️ Missing SL/TP for evaluation: "
                            f"planned_sl={planned_sl}, planned_tp={planned_tp} "
                            f"(source: {sltp_source}, grade accuracy may be reduced)"
                        )

                    # Get entry timestamp from sltp_state or event
                    entry_ts = getattr(self, '_last_entry_timestamp', None)

                    direction = _position_side_name if _position_side_name != 'UNKNOWN' else decision
                    # Normalize direction
                    if direction in ('BUY', 'SELL'):
                        direction = 'LONG' if direction == 'BUY' else 'SHORT'

                    # v11.5: Calculate MAE/MFE from pre-captured extremes (cleared earlier)
                    mae_pct = 0.0
                    mfe_pct = 0.0
                    if _captured_mae_entry > 0:
                        ep = _captured_mae_entry
                        if _captured_mae_is_long:
                            mae_pct = (ep - _captured_mae_min) / ep * 100 if _captured_mae_min < ep else 0.0
                            mfe_pct = (_captured_mae_max - ep) / ep * 100 if _captured_mae_max > ep else 0.0
                        else:
                            mae_pct = (_captured_mae_max - ep) / ep * 100 if _captured_mae_max > ep else 0.0
                            mfe_pct = (ep - _captured_mae_min) / ep * 100 if _captured_mae_min < ep else 0.0

                    evaluation = evaluate_trade(
                        entry_price=entry_price,
                        exit_price=exit_price,
                        planned_sl=planned_sl,
                        planned_tp=planned_tp,
                        direction=direction,
                        pnl_pct=pnl_pct,
                        confidence=confidence,
                        position_size_pct=position_size_pct,
                        entry_timestamp=entry_ts,
                        exit_timestamp=datetime.now(timezone.utc).isoformat(),
                        # v6.0: Position management metrics (use pre-captured values)
                        pyramid_layers_used=_captured_pyramid_layers,
                        partial_close_count=0,
                        confidence_at_exit=_captured_confidence_at_exit,
                        # v11.5: SL/TP optimization data (use pre-captured values)
                        atr_value=_captured_atr_value,
                        sl_atr_multiplier=_captured_sl_atr_mult,
                        is_counter_trend=_captured_is_counter_trend,
                        risk_appetite=_captured_risk_appetite,
                        trend_direction=_captured_trend_direction,
                        adx=_captured_adx,
                        mae_pct=mae_pct,
                        mfe_pct=mfe_pct,
                    )

                    self.log.info(
                        f"📊 Trade evaluation: Grade={evaluation.get('grade', '?')} | "
                        f"R/R planned={evaluation.get('planned_rr', 0):.1f} actual={evaluation.get('actual_rr', 0):.1f} | "
                        f"Exit={evaluation.get('exit_type', '?')}"
                    )
                except Exception as eval_err:
                    # v5.12: Upgraded to ERROR — evaluation failure loses grade data
                    eval_error_reason = str(eval_err)
                    self.log.error(
                        f"❌ Trade evaluation FAILED: {eval_err} "
                        f"(trade will be recorded without grade)"
                    )

                # Record the outcome (with evaluation if available)
                # v5.12: Separate try/except so record_outcome failure is distinguishable
                # v12.0: Include winning_side + entry_judge_summary for reflection
                try:
                    self.multi_agent.record_outcome(
                        decision=decision,
                        pnl=pnl_pct,
                        conditions=conditions,
                        evaluation=evaluation,
                        eval_error_reason=eval_error_reason,
                        close_reason=close_reason,
                    )
                    self.log.info(f"📝 Trade outcome recorded")
                except Exception as record_err:
                    self.log.error(
                        f"❌ Failed to record trade outcome: {record_err} "
                        f"(trade data LOST — this needs investigation)"
                    )
        except Exception as e:
            self.log.error(f"❌ Critical failure in trade outcome recording pipeline: {e}")

    def on_order_canceled(self, event):
        """
        Handle order canceled events.

        v3.10: Track order cancellations for better order lifecycle management.
        v4.0 (A1): Call _handle_orphan_order to detect unprotected positions.
        v5.13/v7.2: Skip orphan handling for orders intentionally cancelled by per-layer SL replacement.
        """
        client_order_id = str(event.client_order_id) if hasattr(event, 'client_order_id') else 'N/A'
        short_id = client_order_id[:8]

        # v5.13/v7.2: Check if this was an intentionally cancelled order (per-layer SL replacement)
        if client_order_id in self._intentionally_cancelled_order_ids:
            self._intentionally_cancelled_order_ids.discard(client_order_id)
            self.log.info(
                f"🗑️ Order canceled (intentional replace): {short_id}... — skipping orphan detection"
            )
            return

        self.log.info(
            f"🗑️ Order canceled: {short_id}... "
            f"(instrument: {getattr(event, 'instrument_id', self.instrument_id)})"
        )

        # v4.17: If this was our pending LIMIT entry, clean up state
        if self._pending_entry_order_id and client_order_id == self._pending_entry_order_id:
            self.log.info(f"📋 Pending LIMIT entry order cancelled: {short_id}...")
            self._pending_entry_order_id = None
            # _pending_sltp and sltp_state already cleaned by _cancel_pending_entry_order
            # but handle external cancellation (e.g., Binance auto-cancel) as well
            if self._pending_sltp:
                self._pending_sltp = None
                instrument_key = str(self.instrument_id)
                if instrument_key in self.sltp_state:
                    state = self.sltp_state[instrument_key]
                    if state.get("sl_order_id") is None:
                        del self.sltp_state[instrument_key]

        # Track in metrics if available
        if hasattr(self, '_order_cancel_count'):
            self._order_cancel_count += 1
        else:
            self._order_cancel_count = 1

        # v7.2: Per-layer order tracking — identify which layer lost its SL/TP
        if client_order_id in self._order_to_layer:
            layer_id = self._order_to_layer[client_order_id]
            layer = self._layer_orders.get(layer_id)
            if layer:
                if layer.get('sl_order_id') == client_order_id:
                    layer['sl_order_id'] = None
                    layer['sl_price'] = 0
                    del self._order_to_layer[client_order_id]
                    self._update_aggregate_sltp_state()
                    self._persist_layer_orders()
                    self._handle_orphan_order(client_order_id, f"Layer {layer_id} SL canceled")
                    return
                elif layer.get('tp_order_id') == client_order_id:
                    # v24.0: Save original TP price before clearing — needed for resubmit
                    original_tp_price = layer.get('tp_price', 0)
                    layer['tp_order_id'] = None
                    layer['tp_price'] = 0
                    del self._order_to_layer[client_order_id]
                    self._update_aggregate_sltp_state()
                    self._persist_layer_orders()
                    self.log.warning(f"⚠️ Layer {layer_id} TP canceled — attempting resubmit")
                    # v24.0: Resubmit at original planned price (mirrors SL emergency recovery)
                    if original_tp_price > 0:
                        self._resubmit_tp_for_layer(layer_id, original_tp_price)
                    return
                elif layer.get('trailing_order_id') == client_order_id:
                    # v24.2: Trailing is advisory — fixed SL still protects position.
                    # Just clear reference to avoid stale mapping.
                    layer['trailing_order_id'] = ''
                    layer.pop('trailing_offset_bps', None)
                    layer.pop('trailing_activation_price', None)
                    del self._order_to_layer[client_order_id]
                    self._update_aggregate_sltp_state()
                    self._persist_layer_orders()
                    sl_price = layer.get('sl_price', 0)
                    self.log.warning(
                        f"⚠️ Layer {layer.get('layer_index', '?')} trailing canceled — "
                        f"fixed SL @ ${sl_price:,.2f} still protects"
                    )
                    return

        # Fallback: legacy aggregate sltp_state check
        instrument_key = str(self.instrument_id)
        state = self.sltp_state.get(instrument_key)
        if state and state.get("sl_order_id") == client_order_id:
            state["sl_order_id"] = None
            self._handle_orphan_order(client_order_id, "SL canceled")

    def on_order_expired(self, event):
        """
        Handle order expired events.

        v3.10: Track GTC order expirations.
        v4.0 (A1): Call _handle_orphan_order to detect unprotected positions.
        v5.13/v7.2: Skip orphan handling for orders intentionally cancelled by per-layer SL replacement.
               STOP_MARKET orders (routed via Binance Algo Order API) report cancellations
               as "expired" events in NT 1.222.0, not "canceled". Without this filter,
               every per-layer SL replacement triggers a false orphan → emergency SL.
        """
        client_order_id = str(event.client_order_id) if hasattr(event, 'client_order_id') else 'N/A'
        short_id = client_order_id[:8]

        # v5.13/v7.2: Check if this was an intentionally cancelled order (per-layer SL replacement)
        if client_order_id in self._intentionally_cancelled_order_ids:
            self._intentionally_cancelled_order_ids.discard(client_order_id)
            self.log.info(
                f"⏰ Order expired (intentional cancel): {short_id}... — skipping orphan detection"
            )
            return

        self.log.warning(
            f"⏰ Order expired: {short_id}... "
            f"(instrument: {getattr(event, 'instrument_id', self.instrument_id)})"
        )

        # Send Telegram alert for unexpected expirations
        if self.telegram_bot and self.enable_telegram:
            try:
                alert_msg = self.telegram_bot.format_error_alert({
                    'level': 'WARNING',
                    'message': f"订单过期: {short_id}...",
                    'context': "GTC 订单异常过期",
                })
                self.telegram_bot.send_message_sync(alert_msg)
            except Exception as e:
                self.log.warning(f"Failed to send Telegram alert for order expiration: {e}")

        # v7.2: Per-layer order tracking — identify which layer lost its SL/TP
        if client_order_id in self._order_to_layer:
            layer_id = self._order_to_layer[client_order_id]
            layer = self._layer_orders.get(layer_id)
            if layer:
                if layer.get('sl_order_id') == client_order_id:
                    layer['sl_order_id'] = None
                    layer['sl_price'] = 0
                    del self._order_to_layer[client_order_id]
                    self._update_aggregate_sltp_state()
                    self._persist_layer_orders()
                    self._handle_orphan_order(client_order_id, f"Layer {layer_id} SL expired")
                    return
                elif layer.get('tp_order_id') == client_order_id:
                    # v24.0: Save original TP price before clearing — needed for resubmit
                    original_tp_price = layer.get('tp_price', 0)
                    layer['tp_order_id'] = None
                    layer['tp_price'] = 0
                    del self._order_to_layer[client_order_id]
                    self._update_aggregate_sltp_state()
                    self._persist_layer_orders()
                    self.log.warning(f"⚠️ Layer {layer_id} TP expired — attempting resubmit")
                    # v24.0: Resubmit at original planned price (mirrors SL emergency recovery)
                    if original_tp_price > 0:
                        self._resubmit_tp_for_layer(layer_id, original_tp_price)
                    return
                elif layer.get('trailing_order_id') == client_order_id:
                    # v24.2: Trailing expired — fixed SL still protects position.
                    layer['trailing_order_id'] = ''
                    layer.pop('trailing_offset_bps', None)
                    layer.pop('trailing_activation_price', None)
                    del self._order_to_layer[client_order_id]
                    self._update_aggregate_sltp_state()
                    self._persist_layer_orders()
                    sl_price = layer.get('sl_price', 0)
                    self.log.warning(
                        f"⚠️ Layer {layer.get('layer_index', '?')} trailing expired — "
                        f"fixed SL @ ${sl_price:,.2f} still protects"
                    )
                    return

        # v4.0 (A1): Fallback — check if position is now unprotected
        self._handle_orphan_order(client_order_id, "GTC expired")

    def on_order_denied(self, event):
        """
        Handle order denied events (system-level denial, before reaching exchange).

        v3.10: This is CRITICAL for bracket/contingent orders where partial failures
        can leave positions unprotected.

        v14.1: Aligned with on_order_rejected — full ID extraction, entry cleanup,
        per-layer cleanup, and orphan detection for automatic SL recovery.

        Common causes:
        - Insufficient margin
        - Risk limit exceeded
        - Rate limiting
        - System pre-trade checks failed

        NautilusTrader docs: "Always handle OrderDenied and OrderRejected events
        in your strategy, especially for contingent orders where partial failures
        can leave positions unprotected."
        """
        reason = getattr(event, 'reason', 'Unknown reason')
        # v14.1 fix: Use full client_order_id (was truncated to [:8], preventing matching)
        client_order_id = str(event.client_order_id) if hasattr(event, 'client_order_id') else 'N/A'

        self.log.error(f"🚫 Order DENIED (pre-exchange): {client_order_id[:8]}... - {reason}")

        # v14.1: If this was our pending LIMIT entry, clean up state
        if self._pending_entry_order_id and client_order_id == self._pending_entry_order_id:
            self.log.warning(f"⚠️ LIMIT entry order denied: {client_order_id[:8]}...")
            self._pending_entry_order_id = None
            if self._pending_sltp:
                self._pending_sltp = None
                instrument_key = str(self.instrument_id)
                if instrument_key in self.sltp_state:
                    state = self.sltp_state[instrument_key]
                    if state.get("sl_order_id") is None:
                        del self.sltp_state[instrument_key]

        # 🚨 CRITICAL: Send immediate Telegram alert
        if self.telegram_bot and self.enable_telegram:
            try:
                alert_msg = self.telegram_bot.format_error_alert({
                    'level': 'CRITICAL',
                    'message': f"订单被拒绝: {reason}",
                    'context': f"订单 ID: {client_order_id[:8]}... (交易所前置拒绝)",
                })
                self.telegram_bot.send_message_sync(alert_msg)
                self.log.info("📱 Telegram alert sent for order denial")
            except Exception as e:
                self.log.warning(f"Failed to send Telegram alert for order denial: {e}")

        # v14.1: Per-layer cleanup + orphan detection (aligned with on_order_rejected)
        self._handle_orphan_order(client_order_id, f"denied: {reason}")

    def on_position_changed(self, event):
        """
        Handle position quantity change events (partial fills, partial closes).

        v7.2: Simplified — per-layer SL/TP are independent, no qty sync needed.
        Each layer's SL/TP orders have their own fixed quantities.
        """
        self.log.info(
            f"📊 Position changed: {event.side.name} "
            f"qty {float(event.quantity)} (signed: {getattr(event, 'signed_qty', 'N/A')})"
        )

        # Update aggregate sltp_state quantity for display
        instrument_key = str(self.instrument_id)
        if instrument_key in self.sltp_state:
            new_qty = float(event.quantity)
            self.sltp_state[instrument_key]["quantity"] = new_qty

    def _format_entry_conditions(self) -> str:
        """
        Format current market conditions for memory recording.

        v3.12: Captures key indicators at entry for pattern learning.
        """
        try:
            parts = []

            # Get cached price for context
            if hasattr(self, '_cached_current_price') and self._cached_current_price:
                parts.append(f"price=${self._cached_current_price:,.0f}")

            # Get technical indicators from indicator_manager
            if hasattr(self, 'indicator_manager') and self.indicator_manager:
                # RSI
                if hasattr(self.indicator_manager, 'rsi') and self.indicator_manager.rsi.initialized:
                    rsi = self.indicator_manager.rsi.value * 100
                    parts.append(f"RSI={rsi:.0f}")

                # MACD direction
                if hasattr(self.indicator_manager, 'macd') and self.indicator_manager.macd.initialized:
                    macd = self.indicator_manager.macd.value
                    macd_signal = self.indicator_manager.macd_signal.value if hasattr(self.indicator_manager, 'macd_signal') else 0
                    macd_dir = "bullish" if macd > macd_signal else "bearish"
                    parts.append(f"MACD={macd_dir}")

                # BB position (requires current price)
                if hasattr(self, '_cached_current_price') and self._cached_current_price:
                    try:
                        tech_data = self.indicator_manager.get_technical_data(self._cached_current_price)
                        bb_pos = tech_data.get('bb_position', 0.5) * 100
                        parts.append(f"BB={bb_pos:.0f}%")
                    except Exception as e:
                        self.log.debug(f"BB position is best-effort for status display: {e}")
                        pass  # BB position is best-effort for status display

            # Get confidence and winning side from last signal
            if hasattr(self, 'last_signal') and self.last_signal:
                confidence = self.last_signal.get('confidence', 'N/A')
                parts.append(f"conf={confidence}")

                judge = self.last_signal.get('judge_decision', {})
                if judge:
                    winning = judge.get('winning_side', 'N/A')
                    parts.append(f"winner={winning}")

            # Get sentiment data if available
            if hasattr(self, 'latest_sentiment_data') and self.latest_sentiment_data:
                ls_ratio = self.latest_sentiment_data.get('long_short_ratio')
                if ls_ratio:
                    sentiment = "crowded_long" if ls_ratio > 2.0 else "crowded_short" if ls_ratio < 0.5 else "neutral"
                    parts.append(f"sentiment={sentiment}")

            return ", ".join(parts) if parts else "N/A"

        except Exception as e:
            self.log.debug(f"Failed to format entry conditions: {e}")
            return "N/A"

    def _cleanup_oco_orphans(self):
        """
        Clean up orphan orders.

        This is a safety mechanism that runs periodically to:
        1. Cancel orphan reduce-only orders when no position exists
        2. Cancel untracked SL orders when position exists but SL total > position qty

        Note: OCO group management is no longer needed as NautilusTrader handles it automatically.
        """
        try:
            # v36.3: Two guards prevent orphan cleanup from destroying just-submitted
            # SL/TP/trailing orders when Binance API lags behind NautilusTrader state.
            #
            # Guard 1: Same-cycle protection. If SL/TP were just submitted in THIS
            # on_timer cycle (e.g., AI decided LONG and executed trade), don't cancel
            # them as orphans. Binance API may not reflect the new position yet.
            if self._sltp_modified_this_cycle:
                self.log.info(
                    "🔍 Orphan cleanup skipped: SL/TP just submitted this cycle"
                )
                return

            # Guard 2: Cross-cycle protection. Ghost detection (v24.1) uses
            # double-confirmation (>120s) before clearing state. If ghost is pending,
            # don't cancel orders — the "no position" from Binance may be API lag.
            _ghost_ts = getattr(self, '_ghost_first_seen', 0.0)
            if _ghost_ts > 0:
                # Safety: detect stale ghost flag. If _layer_orders is empty and
                # no sltp_state, ghost flag has no purpose — clear it and proceed.
                # This prevents permanent orphan cleanup block after position
                # closes via event (on_position_closed clears layers but older
                # code paths might not clear _ghost_first_seen).
                _instrument_key = str(self.instrument_id)
                _has_stale = bool(self._layer_orders) or (_instrument_key in self.sltp_state)
                if not _has_stale:
                    self.log.info(
                        "🔍 Stale ghost flag cleared (no layers/state to protect) — "
                        "proceeding with orphan cleanup"
                    )
                    self._ghost_first_seen = 0.0
                else:
                    self.log.info(
                        "🔍 Orphan cleanup skipped: ghost detection pending confirmation "
                        f"({time.time() - _ghost_ts:.0f}s / 120s)"
                    )
                    return

            # v4.9.1: Use Binance API for accurate position check
            pos_data = self._get_current_position_data()
            has_position = pos_data is not None and pos_data.get('quantity', 0) != 0

            if not has_position:
                # No position but check for orphan orders
                open_orders = self.cache.orders_open(instrument_id=self.instrument_id)

                if open_orders:
                    orphan_count = 0
                    for order in open_orders:
                        if order.is_reduce_only:
                            # This is a reduce-only order without a position - orphan!
                            try:
                                self.cancel_order(order)
                                orphan_count += 1
                                self.log.info(
                                    f"🗑️ Cancelled orphan reduce-only order: "
                                    f"{str(order.client_order_id)[:8]}..."
                                )
                            except Exception as e:
                                self.log.error(
                                    f"Failed to cancel orphan order: {e}"
                                )

                    if orphan_count > 0:
                        self.log.warning(
                            f"⚠️ Cleaned up {orphan_count} orphan orders"
                        )
            else:
                # v15.7: Position exists — check for untracked SL orders
                # (e.g., stale full-position SL from crash recovery coexisting with per-layer SLs)
                self._cleanup_untracked_sl_orders(pos_data)

        except Exception as e:
            self.log.error(f"❌ Orphan order cleanup failed: {e}")

    def _cleanup_untracked_sl_orders(self, pos_data: Dict[str, Any]):
        """
        v15.7: Cancel SL orders not tracked by any layer when total SL qty exceeds position qty.

        Scenario: crash recovery creates a full-position emergency SL, then normal
        per-layer SLs are also restored/created. The emergency SL becomes redundant
        but isn't cancelled, causing SL total > position qty.
        """
        try:
            if not self._layer_orders:
                return

            pos_qty = abs(float(pos_data.get('quantity', 0)))
            if pos_qty <= 0:
                return

            open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
            tracked_sl_ids = set()
            for layer in self._layer_orders.values():
                sl_id = layer.get('sl_order_id')
                if sl_id:
                    tracked_sl_ids.add(sl_id)
                trailing_id = layer.get('trailing_order_id')
                if trailing_id:
                    tracked_sl_ids.add(trailing_id)

            # Find SL orders not tracked by any layer
            untracked_sls = []
            total_sl_qty = 0.0
            for order in open_orders:
                if order.is_reduce_only and order.order_type in (
                    OrderType.STOP_MARKET, OrderType.TRAILING_STOP_MARKET,
                ):
                    order_id = str(order.client_order_id)
                    order_qty = float(order.quantity)
                    total_sl_qty += order_qty
                    if order_id not in tracked_sl_ids:
                        untracked_sls.append(order)

            if not untracked_sls or total_sl_qty <= pos_qty:
                return

            # Cancel untracked SLs (they are redundant — layer SLs already cover the position)
            cancelled = 0
            for order in untracked_sls:
                try:
                    self.cancel_order(order)
                    cancelled += 1
                    self.log.info(
                        f"🗑️ Cancelled untracked SL: {str(order.client_order_id)[:8]}... "
                        f"qty={float(order.quantity):.4f} (not in any layer)"
                    )
                except Exception as e:
                    self.log.error(f"Failed to cancel untracked SL: {e}")

            if cancelled > 0:
                self.log.warning(
                    f"⚠️ Cleaned {cancelled} untracked SL order(s): "
                    f"SL total was {total_sl_qty:.4f} > position {pos_qty:.4f}"
                )
        except Exception as e:
            self.log.debug(f"Untracked SL cleanup check (non-critical): {e}")

    # ===== Remote Control Methods (for Telegram commands) =====
    
