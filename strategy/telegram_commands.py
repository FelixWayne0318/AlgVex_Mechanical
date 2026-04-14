"""
Telegram Command Handlers Mixin

Extracted from ai_strategy.py for code organization.
Contains all Telegram bot command implementations.
"""

import math
import os
import json
import platform
import subprocess
import sys
import threading
from typing import Dict, Any
from datetime import datetime, timedelta, timezone

from nautilus_trader.model.enums import OrderSide, OrderType, TriggerType

from strategy.trading_logic import get_evaluation_summary


class TelegramCommandsMixin:
    """Mixin providing Telegram command handler methods for AITradingStrategy."""

    def handle_telegram_command(self, command: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle Telegram commands.
        
        Parameters
        ----------
        command : str
            Command name (status, position, pause, resume)
        args : dict
            Command arguments
        
        Returns
        -------
        dict
            Response with 'success', 'message', and optional 'error'
        """
        try:
            if command == 'status':
                return self._cmd_status()
            elif command == 'position':
                return self._cmd_position()
            elif command == 'orders':
                return self._cmd_orders()
            elif command == 'history':
                return self._cmd_history()
            elif command == 'risk':
                return self._cmd_risk()
            elif command == 'pause':
                return self._cmd_pause()
            elif command == 'resume':
                return self._cmd_resume()
            elif command == 'close':
                return self._cmd_close()
            elif command == 'daily_summary':
                return self._cmd_daily_summary()
            elif command == 'weekly_summary':
                return self._cmd_weekly_summary()
            elif command == 'balance':
                return self._cmd_balance()
            elif command == 'analyze':
                return self._cmd_analyze()
            elif command == 'config':
                return self._cmd_config()
            elif command == 'version':
                return self._cmd_version()
            elif command == 'logs':
                return self._cmd_logs(args)
            elif command == 'force_analysis':
                return self._cmd_force_analysis()
            elif command == 'partial_close':
                return self._cmd_partial_close(args)
            elif command == 'set_leverage':
                return self._cmd_set_leverage(args)
            elif command == 'toggle':
                return self._cmd_toggle(args)
            elif command == 'set_param':
                return self._cmd_set_param(args)
            elif command == 'restart':
                return self._cmd_restart()
            elif command == 'modify_sl':
                return self._cmd_modify_sl(args)
            elif command == 'modify_tp':
                return self._cmd_modify_tp(args)
            elif command == 'profit':
                return self._cmd_profit()
            elif command == 'reload_config':
                return self._cmd_reload_config()
            elif command == 'calibrate':
                return self._cmd_calibrate()
            elif command == 'layer3':
                return self._cmd_layer3()
            elif command == 'baseline':
                return self._cmd_baseline()
            elif command == 'regime':
                return self._cmd_regime_status()
            else:
                return {
                    'success': False,
                    'error': f"Unknown command: {command}"
                }
        except Exception as e:
            self.log.error(f"Error handling command '{command}': {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _cmd_status(self) -> Dict[str, Any]:
        """Handle /status command - shows REAL account balance."""
        try:

            # Get current price from thread-safe cache
            # IMPORTANT: Do NOT access indicator_manager here - it's called from
            # Telegram thread and Rust indicators (RSI) are not thread-safe
            with self._state_lock:
                current_price = self._cached_current_price

            # Fetch REAL balance from Binance
            real_balance = self.binance_account.get_balance()
            total_balance = real_balance.get('total_balance', 0)

            # v4.9.1: Get unrealized PnL from Binance API (real balance or position data)
            unrealized_pnl = real_balance.get('unrealized_pnl', 0)
            pos_data = self._get_current_position_data(current_price=current_price, from_telegram=True)
            if pos_data and pos_data.get('unrealized_pnl') is not None:
                unrealized_pnl = pos_data['unrealized_pnl']

            # Calculate uptime
            uptime_str = "N/A"
            if self.strategy_start_time:
                uptime_delta = datetime.now(timezone.utc) - self.strategy_start_time
                hours = uptime_delta.total_seconds() // 3600
                minutes = (uptime_delta.total_seconds() % 3600) // 60
                uptime_str = f"{int(hours)}h {int(minutes)}m"

            # Get last signal
            last_signal = "N/A"
            last_signal_time = "N/A"
            if hasattr(self, 'last_signal') and self.last_signal:
                last_signal = f"{self.last_signal.get('signal', 'N/A')} ({self.last_signal.get('confidence', 'N/A')})"

            # Use real balance if available, otherwise fall back to configured equity
            display_equity = total_balance if total_balance > 0 else self.equity

            # v4.7: Get account context for portfolio risk fields
            account_context = self._get_account_context(current_price) if current_price > 0 else {}

            # v5.2: Get SL/TP from sltp_state + Binance fallback
            sl_price = None
            tp_price = None
            position_side = None
            if pos_data:
                position_side = pos_data.get('side', '').upper()
                instrument_key = str(self.instrument_id)
                ts_state = self.sltp_state.get(instrument_key, {})
                sl_price = ts_state.get('current_sl_price')
                tp_price = ts_state.get('current_tp_price')
                # Fallback to Binance orders
                if sl_price is None or tp_price is None:
                    try:
                        symbol = str(self.instrument_id).split('.')[0].replace('-PERP', '')
                        pos_side_lower = position_side.lower() if position_side else 'long'
                        sl_tp = self.binance_account.get_sl_tp_from_orders(symbol, pos_side_lower)
                        if sl_price is None:
                            sl_price = sl_tp.get('sl_price')
                        if tp_price is None:
                            tp_price = sl_tp.get('tp_price')
                    except Exception as e:
                        self.log.debug(f"SL/TP fetch is best-effort for status display: {e}")
                        pass  # SL/TP fetch is best-effort for status display

            status_info = {
                'is_running': True,  # If this method is called, strategy is running
                'is_paused': self.is_trading_paused,
                'instrument_id': str(self.instrument_id),
                'current_price': current_price,
                'equity': display_equity,  # Now shows REAL balance
                'unrealized_pnl': unrealized_pnl,
                'last_signal': last_signal,
                'last_signal_time': last_signal_time,
                'uptime': uptime_str,
                # v5.2: SL/TP & Position info
                'position_side': position_side,
                'sl_price': sl_price,
                'tp_price': tp_price,
                # v4.7: Portfolio Risk Fields (CRITICAL)
                'total_unrealized_pnl_usd': account_context.get('total_unrealized_pnl_usd'),
                'liquidation_buffer_portfolio_min_pct': account_context.get('liquidation_buffer_portfolio_min_pct'),
                'total_daily_funding_cost_usd': account_context.get('total_daily_funding_cost_usd'),
                'can_add_position_safely': account_context.get('can_add_position_safely'),
                # v4.6: Account capacity fields
                'available_margin': account_context.get('available_margin'),
                'used_margin_pct': account_context.get('used_margin_pct'),
                'leverage': account_context.get('leverage'),
            }

            message = self.telegram_bot.format_status_response(status_info) if self.telegram_bot else "Status unavailable"
            
            return {
                'success': True,
                'message': message
            }
        except Exception as e:
            self.log.warning(f"Command failed: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _cmd_position(self) -> Dict[str, Any]:
        """Handle /position command — comprehensive position display."""
        try:
            # Get current price from thread-safe cache FIRST
            # IMPORTANT: Do NOT access indicator_manager here - it's called from
            # Telegram thread and Rust indicators (RSI) are not thread-safe
            with self._state_lock:
                cached_price = self._cached_current_price

            # Get current position - from_telegram=True ensures we NEVER access indicator_manager
            current_position = self._get_current_position_data(current_price=cached_price, from_telegram=True)

            position_info = {
                'has_position': current_position is not None,
            }

            if current_position:
                # Use cached price, fallback to entry price
                current_price = cached_price if cached_price > 0 else current_position['avg_px']

                entry_price = current_position['avg_px']
                quantity = current_position['quantity']
                side = current_position['side'].upper()
                pnl = current_position['unrealized_pnl']
                pnl_pct = (pnl / (entry_price * quantity)) * 100 if entry_price > 0 else 0

                # Notional value (position size in USD)
                notional_value = quantity * current_price

                # ROE = P&L / initial margin (considers leverage)
                leverage = getattr(self, 'leverage', 1)
                initial_margin = notional_value / leverage if leverage > 0 else notional_value
                roe_pct = (pnl / initial_margin) * 100 if initial_margin > 0 else 0

                position_info.update({
                    'side': side,
                    'quantity': quantity,
                    'entry_price': entry_price,
                    'current_price': current_price,
                    'unrealized_pnl': pnl,
                    'pnl_pct': pnl_pct,
                    # v4.9: Position value + leverage + ROE
                    'notional_value': notional_value,
                    'leverage': leverage,
                    'roe_pct': roe_pct,
                    'initial_margin': initial_margin,
                    # v4.7: Liquidation Risk Fields (CRITICAL)
                    'liquidation_price': current_position.get('liquidation_price'),
                    'liquidation_buffer_pct': current_position.get('liquidation_buffer_pct'),
                    'is_liquidation_risk_high': current_position.get('is_liquidation_risk_high', False),
                    # v4.7: Funding Rate Fields (CRITICAL for perpetuals)
                    'funding_rate_current': current_position.get('funding_rate_current'),
                    'daily_funding_cost_usd': current_position.get('daily_funding_cost_usd'),
                    'funding_rate_cumulative_usd': current_position.get('funding_rate_cumulative_usd'),
                    'effective_pnl_after_funding': current_position.get('effective_pnl_after_funding'),
                    # v4.7: Drawdown Attribution Fields
                    'max_drawdown_pct': current_position.get('max_drawdown_pct'),
                    'peak_pnl_pct': current_position.get('peak_pnl_pct'),
                    # v4.5: Position context fields
                    'duration_minutes': current_position.get('duration_minutes'),
                    'entry_confidence': current_position.get('entry_confidence'),
                })

                # v4.9: Fetch SL/TP from Binance open orders (thread-safe, uses REST API)
                try:
                    if self.binance_account:
                        sltp = self.binance_account.get_sl_tp_from_orders(
                            symbol='BTCUSDT',
                            position_side=side.lower(),
                        )
                        position_info['sl_price'] = sltp.get('sl_price')
                        position_info['tp_price'] = sltp.get('tp_price')
                except Exception as e:
                    self.log.debug(f"Failed to fetch SL/TP from orders: {e}")

                # v4.9: Fetch margin data from Binance (thread-safe)
                try:
                    if self.binance_account:
                        balance_data = self.binance_account.get_balance()
                        if balance_data:
                            position_info['available_balance'] = balance_data.get('available_balance', 0)
                            position_info['margin_balance'] = balance_data.get('margin_balance', 0)
                            total = balance_data.get('total_balance', 0)
                            if total > 0:
                                position_info['margin_used_pct'] = ((total - balance_data.get('available_balance', 0)) / total) * 100
                except Exception as e:
                    self.log.debug(f"Failed to fetch balance data: {e}")

            message = self.telegram_bot.format_position_response(position_info) if self.telegram_bot else "Position unavailable"

            return {
                'success': True,
                'message': message
            }
        except Exception as e:
            self.log.warning(f"Command failed: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _cmd_pause(self) -> Dict[str, Any]:
        """Handle /pause command (thread-safe)."""
        try:
            with self._state_lock:
                if self.is_trading_paused:
                    message = self.telegram_bot.format_pause_response(False, "Trading is already paused") if self.telegram_bot else "Already paused"
                else:
                    self.is_trading_paused = True
                    self.log.info("⏸️ Trading paused by Telegram command")
                    message = self.telegram_bot.format_pause_response(True) if self.telegram_bot else "Trading paused"

            return {
                'success': True,
                'message': message
            }
        except Exception as e:
            self.log.warning(f"Command failed: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    def _cmd_resume(self) -> Dict[str, Any]:
        """Handle /resume command (thread-safe)."""
        try:
            with self._state_lock:
                if not self.is_trading_paused:
                    message = self.telegram_bot.format_resume_response(False, "Trading is not paused") if self.telegram_bot else "Not paused"
                else:
                    self.is_trading_paused = False
                    self.log.info("▶️ Trading resumed by Telegram command")
                    message = self.telegram_bot.format_resume_response(True) if self.telegram_bot else "Trading resumed"

            return {
                'success': True,
                'message': message
            }
        except Exception as e:
            self.log.warning(f"Command failed: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    def _cmd_close(self) -> Dict[str, Any]:
        """
        Handle /close command - close current position.

        Thread-safe: Does not access indicator_manager.
        """
        try:
            # v4.9.1: Use Binance API for accurate position data
            pos_data = self._get_current_position_data(from_telegram=True)

            if not pos_data or pos_data.get('quantity', 0) == 0:
                return {
                    'success': True,
                    'message': "ℹ️ *无持仓*\n\n当前没有需要平仓的仓位。"
                }

            quantity = pos_data['quantity']
            side_str = pos_data['side'].upper()

            # Determine closing side (opposite of position)
            if side_str == 'LONG':
                close_side = OrderSide.SELL
            else:
                close_side = OrderSide.BUY

            # v13.0: Set manual close flag BEFORE cancelling orders.
            # Prevents on_position_opened from treating the close fill as a new position
            # when NautilusTrader's internal position state is out of sync with Binance.
            with self._state_lock:
                self._manual_close_in_progress = True

            # Set forced close reason so on_position_closed() shows '手动平仓'
            # instead of relying on price-tolerance guessing (which can misclassify).
            side_cn = "多仓" if side_str == "LONG" else "空仓"
            self._forced_close_reason = (
                'MANUAL',
                f'📱 Telegram 手动平{side_cn}',
            )

            # v3.10: Cancel all pending orders BEFORE closing to prevent -2022 ReduceOnly rejection
            pre_cancel_intentional_ids: set = set()
            try:
                open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
                if open_orders:
                    # v13.0: Mark all cancelled orders as intentional to prevent
                    # orphan detection from submitting emergency SL during close flow.
                    # Track which IDs we added so we can undo them on abort (Fix 3).
                    for o in open_orders:
                        oid = str(o.client_order_id)
                        pre_cancel_intentional_ids.add(oid)
                        self._intentionally_cancelled_order_ids.add(oid)
                    self.log.info(f"🗑️ Cancelling {len(open_orders)} pending orders before close")
                    self.cancel_all_orders(self.instrument_id)
            except Exception as e:
                # v3.10: ABORT close if cancel fails - user should retry.
                # v13.0: Remove order IDs pre-emptively added to intentional set
                #        since cancel_all_orders didn't run — orders are still live.
                with self._state_lock:
                    self._manual_close_in_progress = False
                self._forced_close_reason = None  # Close aborted
                self._intentionally_cancelled_order_ids -= pre_cancel_intentional_ids
                self.log.error(f"❌ Failed to cancel pending orders, aborting close: {e}")
                return {
                    'success': False,
                    'error': f"Failed to cancel pending orders: {str(e)}. Please try again."
                }

            # Submit close order
            # v13.0: Reset flag if submit fails — prevents blocking future position opens
            # v13.1: If cancel_all_orders() succeeded but submit fails, position is naked.
            #        Submit emergency SL immediately to restore protection.
            try:
                self._submit_order(
                    side=close_side,
                    quantity=quantity,
                    reduce_only=True,
                )
            except Exception as e:
                with self._state_lock:
                    self._manual_close_in_progress = False
                self._forced_close_reason = None  # Submit failed, position not closed
                self.log.error(f"❌ Failed to submit close order: {e}")
                # SL/TP were already cancelled by cancel_all_orders().
                # Re-protect the naked position with emergency SL.
                try:
                    pos_still_open = self._get_current_position_data(from_telegram=True)
                    if pos_still_open and pos_still_open.get('quantity', 0) > 0:
                        self.log.warning(
                            f"🚨 Close order failed after SL cancelled — submitting emergency SL "
                            f"for naked {side_str} {quantity:.4f} BTC position"
                        )
                        self._submit_emergency_sl(
                            quantity=pos_still_open['quantity'],
                            position_side=pos_still_open.get('side', side_str).lower(),
                            reason='Telegram close order submission failed after SL/TP cancelled',
                        )
                except Exception as em_e:
                    self.log.error(f"❌ Emergency SL after failed close also failed: {em_e}")
                return {
                    'success': False,
                    'error': f"Failed to submit close order: {str(e)}. Please try again."
                }

            self.log.info(f"🔴 Position closed by Telegram command: {side_str} {quantity:.4f} BTC")

            side_cn = "多仓" if side_str == "LONG" else "空仓"
            with self._state_lock:
                _c_price = self._cached_current_price or 0
            _c_value = quantity * _c_price if _c_price > 0 else 0
            _c_qty_str = f"{quantity:.4f} BTC (${_c_value:,.0f})" if _c_value > 0 else f"{quantity:.4f} BTC"
            return {
                'success': True,
                'message': f"✅ *正在平仓*\n\n"
                          f"平仓方向: {side_cn}\n"
                          f"数量: {_c_qty_str}\n\n"
                          f"⏳ 订单已提交，等待成交..."
            }
        except Exception as e:
            with self._state_lock:
                self._manual_close_in_progress = False
            self._forced_close_reason = None  # Close failed
            self.log.error(f"Error closing position: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    def _cmd_orders(self) -> Dict[str, Any]:
        """
        Handle /orders command - view open orders.

        Thread-safe: Does not access indicator_manager.
        """
        try:
            # Get open orders
            orders = self.cache.orders_open(instrument_id=self.instrument_id)

            if not orders:
                return {
                    'success': True,
                    'message': "ℹ️ *无挂单*\n\n当前没有待处理的订单。"
                }

            msg = f"📋 *挂单列表* ({len(orders)} 个)\n\n"

            for i, order in enumerate(orders, 1):
                order_type = order.order_type.name
                side = order.side.name
                qty = float(order.quantity)

                # v13.0: Distinguish open vs close using reduce_only flag
                if side == "BUY":
                    side_cn = "平空" if order.is_reduce_only else "开多"
                else:
                    side_cn = "平多" if order.is_reduce_only else "开空"

                # Get price for limit/stop orders
                price_str = ""
                if hasattr(order, 'price') and order.price:
                    price_str = f"@ ${float(order.price):,.2f}"
                elif hasattr(order, 'trigger_price') and order.trigger_price:
                    price_str = f"触发价 @ ${float(order.trigger_price):,.2f}"

                # Order status
                status = order.status.name
                reduce_only_tag = " (止损/止盈)" if order.is_reduce_only else ""

                with self._state_lock:
                    _o_price = self._cached_current_price or 0
                _o_value = qty * _o_price if _o_price > 0 else 0
                _o_qty_str = f"{qty:.4f} BTC (${_o_value:,.0f})" if _o_value > 0 else f"{qty:.4f} BTC"
                msg += f"{i}. {side_cn} {order_type}{reduce_only_tag}\n"
                msg += f"   数量: {_o_qty_str} {price_str}\n"
                msg += f"   状态: {status}\n\n"

            return {
                'success': True,
                'message': msg
            }
        except Exception as e:
            self.log.warning(f"Command failed: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    def _cmd_history(self) -> Dict[str, Any]:
        """
        Handle /history command - view recent trade history.

        Thread-safe: Uses Binance API directly.
        """
        try:
            from utils.binance_account import get_binance_fetcher

            # 获取交易对 symbol
            symbol = str(self.instrument_id).split('.')[0] if self.instrument_id else "BTCUSDT"

            # 从 Binance API 获取最近交易
            fetcher = get_binance_fetcher()
            trades = fetcher.get_trades(symbol=symbol, limit=10)

            if not trades:
                return {
                    'success': True,
                    'message': "ℹ️ *无交易记录*\n\n暂无已执行的交易。"
                }

            msg = f"📊 *最近交易记录* (最近 {len(trades)} 笔)\n\n"

            for trade in reversed(trades):  # 最新的在前
                side = trade.get('side', 'UNKNOWN')
                qty = float(trade.get('qty', 0))
                price = float(trade.get('price', 0))
                realized_pnl = float(trade.get('realizedPnl', 0))
                commission = float(trade.get('commission', 0))
                ts = trade.get('time', 0)

                # v13.0: Distinguish open vs close using realizedPnl
                # realizedPnl != 0 → closing trade (平仓), == 0 → opening trade (开仓)
                is_close = (realized_pnl != 0)
                if side == "BUY":
                    side_emoji = "🟢"
                    side_cn = "平空" if is_close else "开多"
                else:
                    side_emoji = "🔴"
                    side_cn = "平多" if is_close else "开空"

                # 格式化时间
                try:
                    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else datetime.now(timezone.utc)
                    time_str = dt.strftime("%m-%d %H:%M")
                except (ValueError, TypeError, OSError) as e:
                    self.log.debug(f"Using default value, original error: {e}")
                    time_str = "N/A"

                pnl_emoji = "📈" if realized_pnl > 0 else ("📉" if realized_pnl < 0 else "➖")
                trade_value = qty * price
                msg += f"{side_emoji} {side_cn} {qty:.4f} BTC (${trade_value:,.0f}) @ ${price:,.2f}\n"
                msg += f"   {pnl_emoji} 盈亏: ${realized_pnl:+,.2f}\n"
                msg += f"   ⏰ 时间: {time_str} UTC\n\n"

            return {
                'success': True,
                'message': msg
            }
        except Exception as e:
            self.log.warning(f"Command failed: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    def _cmd_risk(self) -> Dict[str, Any]:
        """
        Handle /risk command - view risk metrics.

        Thread-safe: Does not access indicator_manager.
        Shows REAL account balance from Binance API.
        """
        try:
            with self._state_lock:
                cached_price = self._cached_current_price

            # Fetch REAL balance from Binance (with cache)
            real_balance = self.binance_account.get_balance()
            total_balance = real_balance.get('total_balance', 0)
            available_balance = real_balance.get('available_balance', 0)
            unrealized_pnl_total = real_balance.get('unrealized_pnl', 0)

            # v4.9.1: Use Binance API for position data
            pos_data = self._get_current_position_data(current_price=cached_price, from_telegram=True)

            msg = "📊 *风险指标*\n\n"

            # Real Account Balance from Binance
            msg += "*账户 (实时)*:\n"
            if total_balance > 0:
                msg += f"• 余额: ${total_balance:,.2f} USDT\n"
                msg += f"• 可用: ${available_balance:,.2f} USDT\n"
                if unrealized_pnl_total != 0:
                    pnl_emoji = "📈" if unrealized_pnl_total >= 0 else "📉"
                    msg += f"• 未实现盈亏: {pnl_emoji} ${unrealized_pnl_total:,.2f}\n"
            else:
                msg += f"• 余额: ⚠️ 无法获取 (配置值: ${self.equity:,.2f})\n"
            msg += f"• 杠杆: {self.leverage}x\n"
            msg += f"• 最大仓位: {self.position_config.get('max_position_ratio', 0.3)*100:.0f}%\n\n"

            # Use real balance for calculations if available, otherwise fall back to configured equity
            effective_equity = total_balance if total_balance > 0 else self.equity

            # Position risk (from Binance API)
            if pos_data and pos_data.get('quantity', 0) != 0:
                qty = pos_data['quantity']
                entry_price = pos_data['avg_px']
                side = pos_data['side'].upper()
                pnl = pos_data.get('unrealized_pnl', 0)
                pnl_pct = pos_data.get('pnl_percentage', 0)

                # Calculate position value
                position_value = qty * cached_price if cached_price > 0 else qty * entry_price

                pnl_emoji = "📈" if pnl >= 0 else "📉"
                side_cn = "多仓" if side == "LONG" else "空仓"

                msg += "*当前持仓*:\n"
                msg += f"• 方向: {side_cn}\n"
                msg += f"• 数量: {qty:.4f} BTC (${position_value:,.2f})\n"
                msg += f"• 开仓价: ${entry_price:,.2f}\n"
                msg += f"• 当前价: ${cached_price:,.2f}\n"
                msg += f"• 盈亏: {pnl_emoji} ${pnl:,.2f} ({pnl_pct:+.2f}%)\n\n"

                # Risk exposure using real balance
                exposure_pct = (position_value / effective_equity) * 100 if effective_equity > 0 else 0
                msg += "*风险敞口*:\n"
                msg += f"• 仓位/余额: {exposure_pct:.1f}%\n"
                msg += f"• 杠杆敞口: {exposure_pct * self.leverage:.1f}%\n"
            else:
                msg += "*当前持仓*: 无\n"
                msg += "*风险敞口*: 0%\n"

            # v3.12: Risk Controller status
            risk_status = self.risk_controller.get_status()
            state_emoji = {
                'ACTIVE': '🟢', 'REDUCED': '🟡', 'HALTED': '🔴', 'COOLDOWN': '⏸️',
            }.get(risk_status['trading_state'], '⚪')

            msg += f"\n*风控状态*:\n"
            msg += f"• 状态: {state_emoji} {risk_status['trading_state']}\n"
            if risk_status['halt_reason']:
                msg += f"• 原因: {risk_status['halt_reason']}\n"
            msg += f"• 回撤: {risk_status['drawdown_pct']:.1f}%\n"
            msg += f"• 今日盈亏: {risk_status['daily_pnl_pct']:+.1f}%\n"
            msg += f"• 连续亏损: {risk_status['consecutive_losses']}次\n"
            msg += f"• 仓位系数: {risk_status['position_multiplier']:.1f}x\n"

            # Strategy settings
            msg += f"\n*策略设置*:\n"
            msg += f"• 最低信心: {self.min_confidence}\n"
            msg += f"• 自动止损止盈: {'✅' if self.enable_auto_sl_tp else '❌'}\n"
            msg += f"• 交易暂停: {'⏸️ 是' if self.is_trading_paused else '▶️ 否'}\n"

            # v6.0: Position Management status
            msg += f"\n*v6.0 仓位管理*:\n"

            # Cooldown status
            if self.cooldown_enabled:
                if self._stoploss_cooldown_until and self._stoploss_cooldown_until > datetime.now(timezone.utc):
                    remaining = (self._stoploss_cooldown_until - datetime.now(timezone.utc)).total_seconds() / 60
                    msg += f"• 冷静期: ⏸️ 剩余 {remaining:.0f} 分钟 ({self._stoploss_cooldown_type})\n"
                else:
                    msg += f"• 冷静期: ▶️ 无\n"
            else:
                msg += f"• 冷静期: ❌ 未启用\n"

            # v21.0: FR consecutive block counter
            if self._fr_consecutive_blocks > 0:
                msg += f"• FR 阻止: ⚠️ 连续 {self._fr_consecutive_blocks}× {self._fr_block_direction.upper()}"
                if self._fr_consecutive_blocks >= 3:
                    msg += " (趋势疲劳激活)"
                msg += "\n"

            # Pyramiding status
            if self.pyramiding_enabled:
                layers = len(self._position_layers) if self._position_layers else 0
                msg += f"• 金字塔: {layers} 层\n"
            else:
                msg += f"• 金字塔: ❌ 未启用\n"

            return {
                'success': True,
                'message': msg
            }
        except Exception as e:
            self.log.warning(f"Command failed: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    def _cmd_daily_summary(self, report_date: str = None) -> Dict[str, Any]:
        """
        Handle /daily command - view daily performance summary (v3.13).

        Thread-safe: Uses thread-safe state and cached data.

        Args:
            report_date: Optional YYYY-MM-DD string. If None, uses current UTC date.
                         Auto-scheduled reports pass yesterday's date.
        """
        try:

            today = report_date or datetime.now(timezone.utc).strftime('%Y-%m-%d')

            # Get real balance from Binance
            real_balance = self.binance_account.get_balance()
            current_equity = real_balance.get('total_balance', self.equity)

            # Calculate stats from session data
            with self._state_lock:
                timer_count = getattr(self, '_timer_count', 0)
                signals_generated = getattr(self, '_signals_generated_today', timer_count)
                signals_executed = getattr(self, '_signals_executed_today', 0)

            # Get trade history for today from memory system
            total_trades = 0
            winning_trades = 0
            losing_trades = 0
            total_pnl = 0.0
            largest_win = 0.0
            largest_loss = 0.0

            # v3.15: Fix variable name - was 'multi_agent_analyzer', should be 'multi_agent'
            today_memories = []
            if hasattr(self, 'multi_agent') and self.multi_agent:
                memories = self.multi_agent.decision_memory
                today_memories = [m for m in memories if m.get('timestamp', '').startswith(today)]

                for m in today_memories:
                    pnl = m.get('pnl', 0)
                    if pnl != 0:  # Only count actual trades
                        total_trades += 1
                        total_pnl += pnl
                        if pnl > 0:
                            winning_trades += 1
                            largest_win = max(largest_win, pnl)
                        else:
                            losing_trades += 1
                            largest_loss = min(largest_loss, pnl)

            # Calculate PnL from equity ($ amount and %)
            starting_equity = getattr(self, '_daily_starting_equity', 0.0) or current_equity
            equity_pnl = current_equity - starting_equity
            pnl_pct = (equity_pnl / starting_equity * 100) if starting_equity > 0 else 0.0

            # v5.1: Compute evaluation stats for today's trades
            eval_stats = {}
            try:
                eval_stats = get_evaluation_summary(today_memories)
            except Exception as e:
                self.log.debug(f"Evaluation stats are best-effort for daily report: {e}")
                pass  # Evaluation stats are best-effort for daily report

            # v12.0: Collect recent reflections for Telegram summary
            recent_reflections = []
            for mem in reversed(today_memories):
                if mem.get('reflection') and mem.get('evaluation'):
                    recent_reflections.append({
                        'grade': mem['evaluation'].get('grade', '?'),
                        'pnl': mem.get('pnl', 0),
                        'reflection': mem['reflection'],
                    })
                if len(recent_reflections) >= 3:
                    break

            summary_data = {
                'date': today,
                'total_trades': total_trades,
                'winning_trades': winning_trades,
                'losing_trades': losing_trades,
                'total_pnl': equity_pnl,  # v11.5: Dollar PnL from equity, not sum of pnl%
                'total_pnl_pct': pnl_pct,
                'largest_win': largest_win,  # Note: these are still pnl% from memory
                'largest_loss': abs(largest_loss),
                'starting_equity': starting_equity,
                'ending_equity': current_equity,
                'signals_generated': signals_generated,
                'signals_executed': signals_executed,
                'evaluation': eval_stats,
                'recent_reflections': recent_reflections,
            }

            if self.telegram_bot:
                msg = self.telegram_bot.format_daily_summary(summary_data)
            else:
                # Fallback simple format
                msg = f"📊 Daily Summary ({today})\n"
                msg += f"Trades: {total_trades} (W: {winning_trades}, L: {losing_trades})\n"
                msg += f"PnL: ${equity_pnl:+,.2f} ({pnl_pct:+.2f}%)\n"
                msg += f"Equity: ${current_equity:,.2f}"

            return {
                'success': True,
                'message': msg
            }
        except Exception as e:
            self.log.error(f"Error in _cmd_daily_summary: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    def _cmd_weekly_summary(self, report_date: 'datetime' = None) -> Dict[str, Any]:
        """
        Handle /weekly command - view weekly performance summary (v3.13).

        Thread-safe: Uses thread-safe state and cached data.

        Args:
            report_date: Optional datetime. If None, uses current UTC time.
                         Auto-scheduled reports pass yesterday (Sunday) to report last week.
        """
        try:

            today = report_date or datetime.now(timezone.utc)
            # Calculate week start (Monday) and end (Sunday)
            week_start = today - timedelta(days=today.weekday())
            week_end = week_start + timedelta(days=6)
            week_start_str = week_start.strftime('%Y-%m-%d')
            week_end_str = week_end.strftime('%Y-%m-%d')

            # Get real balance from Binance
            real_balance = self.binance_account.get_balance()
            current_equity = real_balance.get('total_balance', self.equity)

            # Initialize stats
            total_trades = 0
            winning_trades = 0
            losing_trades = 0
            total_pnl = 0.0
            daily_pnls = {}
            max_drawdown_pct = 0.0

            # v3.15: Fix variable name - was 'multi_agent_analyzer', should be 'multi_agent'
            week_memories = []
            if hasattr(self, 'multi_agent') and self.multi_agent:
                memories = self.multi_agent.decision_memory

                # Filter memories for this week
                for m in memories:
                    ts = m.get('timestamp', '')[:10]  # YYYY-MM-DD
                    if ts >= week_start_str and ts <= week_end_str:
                        week_memories.append(m)
                        pnl = m.get('pnl', 0)
                        if pnl != 0:
                            total_trades += 1
                            total_pnl += pnl
                            if pnl > 0:
                                winning_trades += 1
                            else:
                                losing_trades += 1

                            # Track daily PnL
                            if ts not in daily_pnls:
                                daily_pnls[ts] = 0.0
                            daily_pnls[ts] += pnl

            # Calculate best/worst days and max drawdown from daily PnL curve
            best_day = {'date': 'N/A', 'pnl': 0.0}
            worst_day = {'date': 'N/A', 'pnl': 0.0}
            daily_breakdown = []
            cumulative_pnl = 0.0
            peak_pnl = 0.0

            for date, pnl in sorted(daily_pnls.items()):
                daily_breakdown.append({'date': date, 'pnl': pnl})
                if pnl > best_day['pnl']:
                    best_day = {'date': date, 'pnl': pnl}
                if pnl < worst_day['pnl']:
                    worst_day = {'date': date, 'pnl': pnl}
                # Track cumulative PnL curve for drawdown
                cumulative_pnl += pnl
                if cumulative_pnl > peak_pnl:
                    peak_pnl = cumulative_pnl
                drawdown = peak_pnl - cumulative_pnl
                if drawdown > max_drawdown_pct:
                    max_drawdown_pct = drawdown

            # Calculate averages
            days_with_trades = len(daily_pnls)
            avg_daily_pnl = total_pnl / days_with_trades if days_with_trades > 0 else 0.0

            # Calculate PnL from equity ($ amount and %)
            starting_equity = getattr(self, '_weekly_starting_equity', 0.0) or current_equity
            equity_pnl = current_equity - starting_equity
            pnl_pct = (equity_pnl / starting_equity * 100) if starting_equity > 0 else 0.0

            # v5.1: Compute evaluation stats for this week's trades
            eval_stats = {}
            try:
                eval_stats = get_evaluation_summary(week_memories)
            except Exception as e:
                self.log.debug(f"Evaluation stats are best-effort for weekly report: {e}")
                pass  # Evaluation stats are best-effort for weekly report

            # v12.0: Collect recent reflections for Telegram weekly summary
            recent_reflections = []
            for mem in reversed(week_memories):
                if mem.get('reflection') and mem.get('evaluation'):
                    recent_reflections.append({
                        'grade': mem['evaluation'].get('grade', '?'),
                        'pnl': mem.get('pnl', 0),
                        'reflection': mem['reflection'],
                    })
                if len(recent_reflections) >= 3:
                    break

            summary_data = {
                'week_start': week_start_str,
                'week_end': week_end_str,
                'total_trades': total_trades,
                'winning_trades': winning_trades,
                'losing_trades': losing_trades,
                'total_pnl': equity_pnl,  # v11.5: Dollar PnL from equity
                'total_pnl_pct': pnl_pct,
                'best_day': best_day,
                'worst_day': worst_day,
                'avg_daily_pnl': avg_daily_pnl,
                'starting_equity': starting_equity,
                'ending_equity': current_equity,
                'max_drawdown_pct': max_drawdown_pct,
                'daily_breakdown': daily_breakdown,
                'evaluation': eval_stats,
                'recent_reflections': recent_reflections,
            }

            if self.telegram_bot:
                msg = self.telegram_bot.format_weekly_summary(summary_data)
            else:
                # Fallback simple format
                msg = f"📊 Weekly Summary ({week_start_str} ~ {week_end_str})\n"
                msg += f"Trades: {total_trades} (W: {winning_trades}, L: {losing_trades})\n"
                msg += f"PnL: ${equity_pnl:+,.2f} ({pnl_pct:+.2f}%)\n"
                msg += f"Equity: ${current_equity:,.2f}"

            return {
                'success': True,
                'message': msg
            }
        except Exception as e:
            self.log.error(f"Error in _cmd_weekly_summary: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    # ===== New Commands v3.0 =====

    def _cmd_balance(self) -> Dict[str, Any]:
        """Handle /balance command - detailed account balance."""
        try:
            with self._state_lock:
                cached_price = self._cached_current_price

            real_balance = self.binance_account.get_balance()
            total_balance = real_balance.get('total_balance', 0)
            available_balance = real_balance.get('available_balance', 0)
            unrealized_pnl = real_balance.get('unrealized_pnl', 0)

            account_context = self._get_account_context(cached_price) if cached_price > 0 else {}

            msg = "💰 *账户余额*\n━━━━━━━━━━━━━━━━━━\n\n"

            if total_balance > 0:
                msg += f"*总余额*: ${total_balance:,.2f}\n"
                msg += f"*可用余额*: ${available_balance:,.2f}\n"
                used = total_balance - available_balance
                msg += f"*已用保证金*: ${used:,.2f}\n"
                if unrealized_pnl != 0:
                    pnl_icon = "📈" if unrealized_pnl >= 0 else "📉"
                    msg += f"*未实现盈亏*: {pnl_icon} ${unrealized_pnl:+,.2f}\n"
                msg += f"\n*杠杆*: {self.leverage}x\n"

                # Capacity
                max_pos_ratio = self.position_config.get('max_position_ratio', 0.3)
                max_pos_value = total_balance * max_pos_ratio * self.leverage
                current_pos_value = account_context.get('current_position_value', 0)
                remaining = max_pos_value - current_pos_value

                msg += f"*最大仓位*: ${max_pos_value:,.0f}\n"
                if current_pos_value > 0:
                    msg += f"*当前仓位*: ${current_pos_value:,.0f}\n"
                    msg += f"*剩余容量*: ${remaining:,.0f}\n"

                used_pct = account_context.get('used_margin_pct', 0)
                if used_pct > 0:
                    cap_icon = '🔴' if used_pct > 80 else '🟡' if used_pct > 60 else '🟢'
                    msg += f"\n*保证金使用率*: {cap_icon} {used_pct:.1f}%\n"

                can_add = account_context.get('can_add_position_safely')
                if can_add is not None:
                    msg += f"*可安全加仓*: {'✅ 是' if can_add else '⚠️ 否'}\n"
            else:
                msg += f"⚠️ 无法获取实时余额\n配置余额: ${self.equity:,.2f}\n"

            return {'success': True, 'message': msg}
        except Exception as e:
            self.log.warning(f"Command failed: {e}")
            return {'success': False, 'error': str(e)}

    def _cmd_analyze(self) -> Dict[str, Any]:
        """Handle /analyze command - current technical indicator snapshot."""
        try:
            with self._state_lock:
                cached_price = self._cached_current_price

            msg = "📊 *技术面快照*\n━━━━━━━━━━━━━━━━━━\n\n"
            msg += f"*BTC*: ${cached_price:,.2f}\n\n"

            # Get cached indicator data (thread-safe)
            last_signal = getattr(self, 'last_signal', None)
            latest_sentiment = getattr(self, 'latest_sentiment_data', None)
            last_heartbeat = getattr(self, '_last_heartbeat_data', None)

            # Technical from last heartbeat (cached, thread-safe)
            if last_heartbeat:
                tech = last_heartbeat.get('technical', {})
                rsi = last_heartbeat.get('rsi', 0)
                if rsi:
                    rsi_label = '超买' if rsi > 70 else '超卖' if rsi < 30 else '正常'
                    msg += f"*RSI*: {rsi:.1f} ({rsi_label})\n"
                if tech.get('macd_histogram') is not None:
                    m = tech['macd_histogram']
                    m_icon = '📈' if m > 0 else '📉'
                    msg += f"*MACD*: {m_icon} {m:+.2f}\n"
                if tech.get('trend_direction'):
                    trend_map = {'BULLISH': '🟢 上涨', 'BEARISH': '🔴 下跌', 'NEUTRAL': '⚪ 震荡'}
                    msg += f"*趋势*: {trend_map.get(tech['trend_direction'], tech['trend_direction'])}\n"
                    if tech.get('adx'):
                        msg += f"*ADX*: {tech['adx']:.0f} ({tech.get('adx_regime', '')})\n"
                if tech.get('bb_position') is not None:
                    bb = tech['bb_position'] * 100
                    msg += f"*布林带位置*: {bb:.0f}%\n"
                if tech.get('volume_ratio') is not None:
                    vr = tech['volume_ratio']
                    v_icon = '🔥' if vr > 1.5 else '📊'
                    msg += f"*量比*: {v_icon} {vr:.1f}x\n"

                # Order flow
                of = last_heartbeat.get('order_flow', {})
                if of.get('buy_ratio') is not None:
                    br = of['buy_ratio'] * 100
                    msg += f"\n*买入占比*: {br:.0f}%\n"
                if of.get('cvd_trend'):
                    c_icon = '📈' if of['cvd_trend'] == 'RISING' else '📉'
                    msg += f"*CVD趋势*: {c_icon} {of['cvd_trend']}\n"
                # v19.2: OI×CVD and CVD-Price signals
                _flow_sig = of.get('flow_signals', {})
                if _flow_sig.get('oi_cvd_signal'):
                    msg += f"*OI×CVD*: {_flow_sig['oi_cvd_signal']}\n"
                if _flow_sig.get('cvd_price_signal'):
                    _cvd_cn = _flow_sig.get('cvd_price_cn', '')
                    msg += f"*CVD信号*: {_flow_sig['cvd_price_signal']} ({_cvd_cn})\n"

                # Derivatives
                deriv = last_heartbeat.get('derivatives', {})
                fr = deriv.get('funding_rate_pct')
                if fr is not None:
                    msg += f"\n*已结算费率*: {fr:.4f}%\n"
                pr = deriv.get('predicted_rate_pct')
                if pr is not None:
                    msg += f"*预期费率*: {pr:.4f}%\n"
                oi = deriv.get('oi_change_pct')
                if oi is not None:
                    msg += f"*OI变化*: {oi:+.1f}%\n"
            else:
                msg += "⚠️ 暂无技术数据 (等待第一次分析)\n"

            # Sentiment
            if latest_sentiment and latest_sentiment.get('long_short_ratio'):
                ls = latest_sentiment['long_short_ratio']
                msg += f"\n*多空比*: {ls:.2f}\n"

            # Last signal
            if last_signal:
                sig = last_signal.get('signal', 'N/A')
                conf = last_signal.get('confidence', 'N/A')
                sig_icon = {'LONG': '🟢', 'SHORT': '🔴', 'HOLD': '⚪'}.get(sig, '❓')
                msg += f"\n*上次信号*: {sig_icon} {sig} ({conf})\n"
                reason = last_signal.get('reasoning', '')
                if reason:
                    msg += f"_原因: {reason}_\n"

            return {'success': True, 'message': msg}
        except Exception as e:
            self.log.warning(f"Command failed: {e}")
            return {'success': False, 'error': str(e)}

    def _cmd_config(self) -> Dict[str, Any]:
        """Handle /config command - show current strategy configuration."""
        try:
            msg = "⚙️ *当前配置*\n━━━━━━━━━━━━━━━━━━\n\n"

            # Trading
            msg += "*交易*:\n"
            msg += f"  品种: {self.instrument_id}\n"
            msg += f"  杠杆: {self.leverage}x\n"
            msg += f"  最低信心: {self.min_confidence}\n"
            msg += f"  允许反转: {'✅' if self.allow_reversals else '❌'}\n"
            msg += f"  暂停状态: {'⏸️ 是' if self.is_trading_paused else '▶️ 否'}\n"

            # Position
            msg += f"\n*仓位管理*:\n"
            method = self.position_config.get('method', 'ai_controlled')
            msg += f"  方法: {method}\n"
            msg += f"  最大比例: {self.position_config.get('max_position_ratio', 0.3)*100:.0f}%\n"

            # Risk
            msg += f"\n*风险管理*:\n"
            msg += f"  自动SL/TP: {'✅' if self.enable_auto_sl_tp else '❌'}\n"
            msg += f"  Bracket订单: {'✅' if self.enable_oco else '❌'}\n"

            # Features
            msg += f"\n*功能模块*:\n"
            msg += f"  多时间框架: {'✅' if self.mtf_enabled else '❌'}\n"
            msg += f"  情绪数据: {'✅' if self.sentiment_enabled else '❌'}\n"
            msg += f"  Telegram: {'✅' if self.enable_telegram else '❌'}\n"

            # v47.0: Strategy mode
            msg += f"\n*策略模式*:\n"
            msg += f"  模式: 机械评分\n"
            msg += f"  评分: 预判三维 S/D/F\n"

            # Timer
            timer_sec = getattr(self.config, 'timer_interval_sec', 1200)
            msg += f"\n*定时器*: {timer_sec}s ({timer_sec//60}分钟)\n"
            msg += f"*分析次数*: {getattr(self, '_timer_count', 0)}\n"

            return {'success': True, 'message': msg}
        except Exception as e:
            self.log.warning(f"Command failed: {e}")
            return {'success': False, 'error': str(e)}

    def _cmd_version(self) -> Dict[str, Any]:
        """Handle /version command - bot version and system info."""
        try:
            uptime_str = "N/A"
            if self.strategy_start_time:
                uptime_delta = datetime.now(timezone.utc) - self.strategy_start_time
                days = uptime_delta.days
                hours = (uptime_delta.total_seconds() % 86400) // 3600
                minutes = (uptime_delta.total_seconds() % 3600) // 60
                if days > 0:
                    uptime_str = f"{days}d {int(hours)}h {int(minutes)}m"
                else:
                    uptime_str = f"{int(hours)}h {int(minutes)}m"

            msg = "🤖 *系统信息*\n━━━━━━━━━━━━━━━━━━\n\n"
            msg += f"*品种*: {self.instrument_id}\n"
            msg += f"*运行时间*: {uptime_str}\n"
            msg += f"*分析次数*: {getattr(self, '_timer_count', 0)}\n"
            msg += f"*交易次数*: {len(getattr(self, 'trade_history', []))}\n\n"
            msg += f"*Python*: {sys.version.split()[0]}\n"
            msg += f"*系统*: {platform.system()} {platform.release()}\n"
            try:
                import nautilus_trader
                nt_ver = getattr(nautilus_trader, '__version__', '1.224.0')
            except Exception:
                nt_ver = '1.224.0'
            msg += f"*NautilusTrader*: {nt_ver}\n"

            return {'success': True, 'message': msg}
        except Exception as e:
            self.log.warning(f"Command failed: {e}")
            return {'success': False, 'error': str(e)}

    def _cmd_logs(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle /logs command - show recent log lines."""
        try:
            lines_count = args.get('lines', 20)
            if lines_count > 50:
                lines_count = 50

            # Try common log file locations
            log_paths = [
                'logs/nautilus_trader.log',
                'logs/trading.log',
                '/tmp/nautilus_trader.log',
            ]

            log_content = None
            used_path = None

            for path in log_paths:
                if os.path.exists(path):
                    with open(path, 'r', errors='replace') as f:
                        all_lines = f.readlines()
                        log_content = all_lines[-lines_count:]
                        used_path = path
                    break

            if log_content:
                msg = f"📋 *最近 {len(log_content)} 行日志*\n"
                msg += f"📁 `{used_path}`\n\n"
                msg += "```\n"
                for line in log_content:
                    clean = line.rstrip()
                    msg += clean + "\n"
                msg += "```"
            else:
                # Fallback: use journalctl if no log file found
                msg = "📋 *日志*\n\n"
                msg += "⚠️ 未找到日志文件\n"
                msg += "可在服务器运行: `journalctl -u nautilus-trader -n 20`\n"

            return {'success': True, 'message': msg}
        except Exception as e:
            self.log.warning(f"Command failed: {e}")
            return {'success': False, 'error': str(e)}

    def _cmd_force_analysis(self) -> Dict[str, Any]:
        """
        Handle /force_analysis command - trigger immediate AI analysis.

        v15.6: Uses NautilusTrader clock.set_time_alert() to schedule a one-shot
        on_timer call on the event loop thread (~2s delay). Previous implementation
        set _force_analysis_requested flag but never read it (dead code).
        """
        try:
            # v13.0: Use lock for atomic read-check-set to prevent race with _cmd_pause
            with self._state_lock:
                if self.is_trading_paused:
                    return {
                        'success': False,
                        'error': '交易已暂停，无法触发分析。请先 /resume'
                    }

            # v15.6: Schedule immediate one-shot alert on NT event loop thread.
            # set_time_alert fires on the event loop (same thread as on_timer),
            # so the _timer_lock guard in on_timer prevents overlap with a
            # scheduled cycle. callback=self.on_timer reuses the full analysis path.
            import time as _time
            alert_name = f"force_analysis_{int(_time.time())}"
            self.clock.set_time_alert(
                name=alert_name,
                alert_time=self.clock.utc_now() + timedelta(seconds=2),
                callback=self.on_timer,
            )
            self.log.info("🔄 Force analysis scheduled via set_time_alert (2s)")

            return {
                'success': True,
                'message': "🔄 *立即分析*\n\n"
                          "已调度立即 AI 分析。\n"
                          "⏳ 约 2 秒后开始执行..."
            }
        except Exception as e:
            self.log.warning(f"Force analysis scheduling failed: {e}")
            return {'success': False, 'error': f"调度失败: {str(e)}"}

    def _cmd_partial_close(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle /partial_close command - close percentage of position."""
        try:
            pct = args.get('percent', 50)
            if pct <= 0 or pct > 100:
                return {'success': False, 'error': '百分比必须在 1-100 之间'}

            pos_data = self._get_current_position_data(from_telegram=True)

            if not pos_data or pos_data.get('quantity', 0) == 0:
                return {
                    'success': True,
                    'message': "ℹ️ *无持仓*\n\n当前没有需要平仓的仓位。"
                }

            full_qty = pos_data['quantity']
            close_qty = full_qty * (pct / 100)
            side_str = pos_data['side'].upper()

            # Round to instrument precision (dynamic step_size)
            _step = float(self.instrument.size_increment)
            close_qty = math.floor(close_qty / _step) * _step
            _min_qty = float(self.instrument.min_quantity) if self.instrument.min_quantity else self.position_config.get('min_trade_amount', 0.001)
            if close_qty < _min_qty:
                return {
                    'success': False,
                    'error': f'平仓数量 {close_qty:.4f} 低于最小交易量'
                }

            close_side = OrderSide.SELL if side_str == 'LONG' else OrderSide.BUY

            # Cancel SL/TP orders if closing more than 50%
            # Track whether we cancelled protection so we can re-protect on failure
            cancelled_protection = False
            if pct > 50:
                try:
                    open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
                    if open_orders:
                        # v13.0: Mark cancelled orders as intentional to prevent
                        # orphan handler from submitting emergency SL during partial close
                        for o in open_orders:
                            self._intentionally_cancelled_order_ids.add(str(o.client_order_id))
                        self.log.info(f"🗑️ Cancelling {len(open_orders)} orders before partial close ({pct}%)")
                        self.cancel_all_orders(self.instrument_id)
                        cancelled_protection = True
                except Exception as e:
                    self.log.warning(f"Failed to cancel orders before partial close: {e}")

            # v14.1: Track partial close for subscriber broadcast notification.
            # on_order_filled() will detect this and send broadcast=True.
            with self._state_lock:
                self._pending_partial_close_broadcast = {
                    'side': side_str,
                    'old_qty': full_qty,
                    'close_qty': close_qty,
                    'pct': pct,
                }

            # v13.1: Wrap submit in try/except — if SL/TP were already cancelled and
            # partial close submission fails, the remaining position is naked.
            # Submit emergency SL to restore protection.
            try:
                self._submit_order(
                    side=close_side,
                    quantity=close_qty,
                    reduce_only=True,
                )
            except Exception as e:
                self.log.error(f"❌ Partial close order submission failed: {e}")
                with self._state_lock:
                    self._pending_partial_close_broadcast = None  # v14.1: Clear on failure
                if cancelled_protection:
                    # Re-protect the remaining position (orders were already cancelled)
                    try:
                        pos_still_open = self._get_current_position_data(from_telegram=True)
                        if pos_still_open and pos_still_open.get('quantity', 0) > 0:
                            self.log.warning(
                                f"🚨 Partial close failed after SL cancelled — submitting emergency SL "
                                f"for naked {side_str} {pos_still_open['quantity']:.4f} BTC position"
                            )
                            self._submit_emergency_sl(
                                quantity=pos_still_open['quantity'],
                                position_side=pos_still_open.get('side', side_str).lower(),
                                reason='Partial close submission failed after SL/TP cancelled',
                            )
                    except Exception as em_e:
                        self.log.error(f"❌ Emergency SL after failed partial close also failed: {em_e}")
                raise  # Re-raise so outer except returns failure response

            self.log.info(f"📉 Partial close ({pct}%) by Telegram: {close_qty:.4f} BTC")

            # v13.1-fix: After successful partial close with cancelled protection,
            # submit emergency SL for the remaining position to close the protection gap.
            # Without this, the remaining position is naked until next on_timer (up to 20 min).
            if cancelled_protection and pct < 100:
                remaining_qty = full_qty - close_qty
                if remaining_qty > 0:
                    try:
                        # Clear stale layer tracking (all SL/TP orders were cancelled)
                        self._layer_orders.clear()
                        self._order_to_layer.clear()
                        self._next_layer_idx = 0
                        self._persist_layer_orders()

                        self._submit_emergency_sl(
                            quantity=remaining_qty,
                            position_side=side_str.lower(),
                            reason=f'Re-protect after partial close {pct}% (SL/TP were cancelled)',
                        )
                        self.log.info(
                            f"🛡️ Emergency SL submitted for remaining {remaining_qty:.4f} BTC "
                            f"after partial close {pct}%"
                        )
                    except Exception as e:
                        self.log.error(
                            f"⚠️ Failed to re-protect remaining position after partial close: {e}"
                        )

            side_cn = "多仓" if side_str == "LONG" else "空仓"
            with self._state_lock:
                _pc_price = self._cached_current_price or 0
            _pc_close_val = close_qty * _pc_price if _pc_price > 0 else 0
            _pc_remain_val = (full_qty - close_qty) * _pc_price if _pc_price > 0 else 0
            _pc_close_str = f"{close_qty:.4f} / {full_qty:.4f} BTC (${_pc_close_val:,.0f})" if _pc_close_val > 0 else f"{close_qty:.4f} / {full_qty:.4f} BTC"
            _pc_remain_str = f"{full_qty - close_qty:.4f} BTC (${_pc_remain_val:,.0f})" if _pc_remain_val > 0 else f"{full_qty - close_qty:.4f} BTC"
            return {
                'success': True,
                'message': f"📉 *部分平仓 {pct}%*\n\n"
                          f"方向: {side_cn}\n"
                          f"平仓: {_pc_close_str}\n"
                          f"剩余: {_pc_remain_str}\n\n"
                          f"⏳ 订单已提交..."
            }
        except Exception as e:
            self.log.error(f"Error in partial close: {e}")
            return {'success': False, 'error': str(e)}

    def _cmd_set_leverage(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle /set_leverage command - change leverage."""
        try:
            new_leverage = args.get('value')
            if new_leverage is None:
                return {'success': False, 'error': '请指定杠杆倍数，例如: /set_leverage 10'}

            new_leverage = int(new_leverage)
            if new_leverage < 1 or new_leverage > self.max_leverage_limit:
                return {'success': False, 'error': f'杠杆倍数必须在 1-{self.max_leverage_limit} 之间'}

            # Check if has open position
            pos_data = self._get_current_position_data(from_telegram=True)
            if pos_data and pos_data.get('quantity', 0) != 0:
                return {
                    'success': False,
                    'error': '有持仓时不能修改杠杆。请先平仓。'
                }

            old_leverage = self.leverage

            self.leverage = new_leverage
            self.log.info(f"⚙️ Leverage changed via Telegram: {old_leverage}x → {new_leverage}x")

            return {
                'success': True,
                'message': f"⚙️ *杠杆已修改*\n\n"
                          f"{old_leverage}x → *{new_leverage}x*\n\n"
                          f"⚠️ 新杠杆将在下次开仓时生效"
            }
        except Exception as e:
            self.log.warning(f"Command failed: {e}")
            return {'success': False, 'error': str(e)}

    def _cmd_toggle(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle /toggle command - toggle feature on/off."""
        try:
            feature = args.get('feature', '').lower()

            toggleable = {
                'sentiment': ('sentiment_enabled', '情绪数据'),
                'mtf': ('mtf_enabled', '多时间框架'),
                'auto_sltp': ('enable_auto_sl_tp', '自动止损止盈'),
                'reversal': ('allow_reversals', '允许反转'),
            }

            if not feature or feature not in toggleable:
                msg = "🔧 *可切换功能*\n\n"
                for key, (attr, name) in toggleable.items():
                    current = getattr(self, attr, False)
                    icon = '✅' if current else '❌'
                    msg += f"  `{key}` — {name} {icon}\n"
                msg += f"\n用法: `/toggle trailing`"
                return {'success': True, 'message': msg}

            attr_name, feature_name = toggleable[feature]
            # v13.0: Use state lock to prevent race with on_timer thread
            with self._state_lock:
                current_value = getattr(self, attr_name, False)
                new_value = not current_value
                setattr(self, attr_name, new_value)

            icon = '✅' if new_value else '❌'
            action = '开启' if new_value else '关闭'
            self.log.info(f"🔧 Feature toggled via Telegram: {feature_name} → {action}")

            return {
                'success': True,
                'message': f"🔧 *{feature_name}* — {icon} {action}\n"
            }
        except Exception as e:
            self.log.warning(f"Command failed: {e}")
            return {'success': False, 'error': str(e)}

    def _cmd_set_param(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle /set command - modify runtime parameters."""
        try:
            param = args.get('param', '').lower()
            value = args.get('value')

            settable = {
                'min_confidence': {
                    'attr': 'min_confidence',
                    'name': '最低信心',
                    'valid': ['LOW', 'MEDIUM', 'HIGH'],
                    'type': 'str',
                },
            }

            if not param or param not in settable:
                msg = "⚙️ *可修改参数*\n\n"
                for key, info in settable.items():
                    current = getattr(self, info['attr'], 'N/A')
                    display_fn = info.get('display', str)
                    msg += f"  `{key}` — {info['name']}: {display_fn(current)}\n"
                    if 'valid' in info:
                        msg += f"    可选: {', '.join(info['valid'])}\n"
                    if 'range' in info:
                        lo, hi = info['range']
                        msg += f"    范围: {lo} - {hi}\n"
                msg += f"\n用法: `/set min_confidence HIGH`"
                return {'success': True, 'message': msg}

            if value is None:
                return {'success': False, 'error': f'请指定值，例如: /set {param} VALUE'}

            info = settable[param]
            old_value = getattr(self, info['attr'], None)

            # v13.0: Use state lock to prevent race with on_timer thread
            if info['type'] == 'str':
                value = str(value).upper()
                if 'valid' in info and value not in info['valid']:
                    return {'success': False, 'error': f"无效值: {value}。可选: {', '.join(info['valid'])}"}
                with self._state_lock:
                    setattr(self, info['attr'], value)
            elif info['type'] == 'float':
                try:
                    value = float(value)
                except ValueError:
                    self.log.warning("Command failed with unknown error")
                    return {'success': False, 'error': f'无效数值: {value}'}
                if 'range' in info:
                    lo, hi = info['range']
                    if value < lo or value > hi:
                        return {'success': False, 'error': f'超出范围: {lo} - {hi}'}
                with self._state_lock:
                    setattr(self, info['attr'], value)

            display_fn = info.get('display', str)
            self.log.info(f"⚙️ Param changed via Telegram: {info['name']} {display_fn(old_value)} → {display_fn(value)}")

            return {
                'success': True,
                'message': f"⚙️ *{info['name']}*\n\n"
                          f"{display_fn(old_value)} → *{display_fn(value)}*\n"
            }
        except Exception as e:
            self.log.warning(f"Command failed: {e}")
            return {'success': False, 'error': str(e)}

    def _cmd_restart(self) -> Dict[str, Any]:
        """Handle /restart command - schedule service restart."""
        try:
            self.log.warning("🔄 Service restart requested via Telegram")

            # Send notification before restart
            if self.telegram_bot:
                self.telegram_bot.send_message_sync(
                    f"🔄 *正在重启服务...*\n\n⏳ 预计 30 秒后恢复\n\n"
                    f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC",
                    use_queue=False,
                )

            # Use systemctl to restart (runs in background)
            subprocess.Popen(
                ['sudo', 'systemctl', 'restart', 'nautilus-trader'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            return {
                'success': True,
                'message': "🔄 *重启已触发*\n\n"
                          "服务正在重启，预计 30 秒后恢复。\n"
                          "请稍后使用 /s 检查状态。"
            }
        except Exception as e:
            self.log.warning(f"Command failed: {e}")
            return {'success': False, 'error': str(e)}

    def _cmd_modify_sl(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle /modify_sl command - modify stop loss price."""
        try:
            new_price = args.get('price')
            if new_price is None:
                return {'success': False, 'error': '请指定止损价格，例如: /modify_sl 95000'}

            new_price = float(new_price)
            if new_price <= 0:
                return {'success': False, 'error': '价格必须大于 0'}

            pos_data = self._get_current_position_data(from_telegram=True)
            if not pos_data or pos_data.get('quantity', 0) == 0:
                return {'success': True, 'message': "ℹ️ *无持仓*\n\n当前没有持仓，无法修改止损。"}

            side = pos_data['side'].upper()
            entry_price = pos_data['avg_px']
            quantity = pos_data['quantity']

            # Validate SL price direction
            if side == 'LONG' and new_price >= entry_price:
                return {'success': False, 'error': f'多仓止损必须低于入场价 ${entry_price:,.2f}'}
            if side == 'SHORT' and new_price <= entry_price:
                return {'success': False, 'error': f'空仓止损必须高于入场价 ${entry_price:,.2f}'}

            # Find and cancel existing SL order
            open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
            sl_cancelled = False
            for order in open_orders:
                if order.is_reduce_only and order.order_type in (
                    OrderType.STOP_MARKET, OrderType.TRAILING_STOP_MARKET,
                ):
                    try:
                        # v5.13: Track intentionally cancelled order IDs
                        self._intentionally_cancelled_order_ids.add(str(order.client_order_id))
                        self.cancel_order(order)
                        sl_cancelled = True
                        self.log.info(f"🗑️ Cancelled old SL order: {str(order.client_order_id)[:8]}")
                    except Exception as e:
                        self.log.warning(f"Failed to cancel old SL: {e}")

            # Create and submit new SL order — wrapped in try/except for v13.1 safety
            sl_side = OrderSide.SELL if side == 'LONG' else OrderSide.BUY
            new_qty = self.instrument.make_qty(quantity)
            try:
                new_sl_order = self.order_factory.stop_market(
                    instrument_id=self.instrument_id,
                    order_side=sl_side,
                    quantity=new_qty,
                    trigger_price=self.instrument.make_price(new_price),
                    trigger_type=TriggerType.LAST_PRICE,
                    reduce_only=True,
                )
                self.submit_order(new_sl_order)
            except Exception as e:
                # v13.1 safety: old SL was cancelled but new SL failed → naked position
                self.log.error(f"🚨 New SL submission failed after cancelling old SL: {e}")
                if sl_cancelled:
                    position_side = 'long' if side == 'LONG' else 'short'
                    self._submit_emergency_sl(quantity, position_side,
                                              reason="modify_sl new SL submit failed")
                return {'success': False, 'error': f'新SL提交失败，已触发紧急止损保护: {e}'}

            # v7.2-fix: Update _layer_orders to keep layer tracking consistent.
            # /modify_sl replaces ALL layers' SL with one aggregated order.
            # For multi-layer positions, each layer gets its own SL order to
            # maintain correct _order_to_layer mapping (dict key uniqueness).
            new_sl_id = str(new_sl_order.client_order_id)
            layer_ids = list(self._layer_orders.keys())
            first_layer = True
            for layer_id in layer_ids:
                layer = self._layer_orders[layer_id]
                old_sl_id = layer.get('sl_order_id', '')
                if old_sl_id:
                    self._order_to_layer.pop(old_sl_id, None)
                # v24.0: Reset trailing — manual SL override replaces trailing
                old_trailing_id = layer.get('trailing_order_id', '')
                if old_trailing_id:
                    self._order_to_layer.pop(old_trailing_id, None)
                layer['trailing_order_id'] = ''
                layer.pop('trailing_offset_bps', None)
                layer.pop('trailing_activation_price', None)

                if first_layer:
                    # First layer uses the already-submitted SL order
                    layer['sl_order_id'] = new_sl_id
                    layer['sl_price'] = new_price
                    self._order_to_layer[new_sl_id] = layer_id
                    first_layer = False
                else:
                    # Additional layers: submit separate SL orders for correct mapping
                    try:
                        layer_qty = self.instrument.make_qty(layer.get('quantity', 0))
                        extra_sl = self.order_factory.stop_market(
                            instrument_id=self.instrument_id,
                            order_side=sl_side,
                            quantity=layer_qty,
                            trigger_price=self.instrument.make_price(new_price),
                            trigger_type=TriggerType.LAST_PRICE,
                            reduce_only=True,
                        )
                        self.submit_order(extra_sl)
                        extra_sl_id = str(extra_sl.client_order_id)
                        layer['sl_order_id'] = extra_sl_id
                        layer['sl_price'] = new_price
                        self._order_to_layer[extra_sl_id] = layer_id
                    except Exception as e:
                        self.log.warning(f"⚠️ Failed to submit per-layer SL for {layer_id}: {e}")
                        layer['sl_order_id'] = new_sl_id
                        layer['sl_price'] = new_price
                        self._order_to_layer[new_sl_id] = layer_id
            if self._layer_orders:
                self._update_aggregate_sltp_state()
                self._persist_layer_orders()
                self.log.info(
                    f"📊 Updated {len(self._layer_orders)} layers with new SL "
                    f"@ ${new_price:,.2f}"
                )

            # Update trailing stop state (fallback for non-layer mode)
            instrument_key = str(self.instrument_id)
            if instrument_key in self.sltp_state:
                self.sltp_state[instrument_key]["sl_order_id"] = new_sl_id
                self.sltp_state[instrument_key]["current_sl"] = new_price

            # v5.13: Mark SL/TP as modified — prevent orphan cascade from async cancel
            self._sltp_modified_this_cycle = True

            self.log.info(f"✅ SL modified via Telegram: ${new_price:,.2f}")

            return {
                'success': True,
                'message': f"✅ *止损已修改*\n\n"
                          f"🛑 新止损: ${new_price:,.2f}\n"
                          f"{'已替换' if sl_cancelled else '⚠️ 未找到旧SL，已创建新SL'}\n\n"
                          f"仓位: {self.telegram_bot.side_to_cn(side, 'position')} {quantity:.4f} BTC (${quantity * entry_price:,.0f})\n"
                          f"入场价: ${entry_price:,.2f}"
            }
        except Exception as e:
            self.log.error(f"Error modifying SL: {e}")
            return {'success': False, 'error': str(e)}

    def _cmd_modify_tp(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle /modify_tp command - modify take profit price."""
        try:
            new_price = args.get('price')
            if new_price is None:
                return {'success': False, 'error': '请指定止盈价格，例如: /modify_tp 105000'}

            new_price = float(new_price)
            if new_price <= 0:
                return {'success': False, 'error': '价格必须大于 0'}

            pos_data = self._get_current_position_data(from_telegram=True)
            if not pos_data or pos_data.get('quantity', 0) == 0:
                return {'success': True, 'message': "ℹ️ *无持仓*\n\n当前没有持仓，无法修改止盈。"}

            side = pos_data['side'].upper()
            entry_price = pos_data['avg_px']
            quantity = pos_data['quantity']

            # Validate TP price direction
            if side == 'LONG' and new_price <= entry_price:
                return {'success': False, 'error': f'多仓止盈必须高于入场价 ${entry_price:,.2f}'}
            if side == 'SHORT' and new_price >= entry_price:
                return {'success': False, 'error': f'空仓止盈必须低于入场价 ${entry_price:,.2f}'}

            # Find and cancel existing TP order
            open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
            tp_cancelled = False
            for order in open_orders:
                if order.is_reduce_only and order.order_type == OrderType.LIMIT_IF_TOUCHED:
                    try:
                        # v5.13: Track intentionally cancelled order IDs
                        self._intentionally_cancelled_order_ids.add(str(order.client_order_id))
                        self.cancel_order(order)
                        tp_cancelled = True
                        self.log.info(f"🗑️ Cancelled old TP order: {str(order.client_order_id)[:8]}")
                    except Exception as e:
                        self.log.warning(f"Failed to cancel old TP: {e}")

            # Create and submit new TP order — wrapped in try/except for safety
            tp_side = OrderSide.SELL if side == 'LONG' else OrderSide.BUY
            new_qty = self.instrument.make_qty(quantity)
            try:
                new_tp_order = self.order_factory.limit_if_touched(
                    instrument_id=self.instrument_id,
                    order_side=tp_side,
                    quantity=new_qty,
                    price=self.instrument.make_price(new_price),
                    trigger_price=self.instrument.make_price(new_price),
                    trigger_type=TriggerType.LAST_PRICE,
                    reduce_only=True,
                )
                self.submit_order(new_tp_order)
            except Exception as e:
                self.log.error(f"🚨 New TP submission failed after cancelling old TP: {e}")
                return {'success': False, 'error': f'新TP提交失败: {e}'}

            # v7.2-fix: Update _layer_orders to keep layer tracking consistent.
            # Per-layer TP orders for correct _order_to_layer mapping.
            new_tp_id = str(new_tp_order.client_order_id)
            layer_ids = list(self._layer_orders.keys())
            first_layer = True
            for layer_id in layer_ids:
                layer = self._layer_orders[layer_id]
                old_tp_id = layer.get('tp_order_id', '')
                if old_tp_id:
                    self._order_to_layer.pop(old_tp_id, None)

                if first_layer:
                    layer['tp_order_id'] = new_tp_id
                    layer['tp_price'] = new_price
                    self._order_to_layer[new_tp_id] = layer_id
                    first_layer = False
                else:
                    try:
                        layer_qty = self.instrument.make_qty(layer.get('quantity', 0))
                        extra_tp = self.order_factory.limit_if_touched(
                            instrument_id=self.instrument_id,
                            order_side=tp_side,
                            quantity=layer_qty,
                            price=self.instrument.make_price(new_price),
                            trigger_price=self.instrument.make_price(new_price),
                            trigger_type=TriggerType.LAST_PRICE,
                            reduce_only=True,
                        )
                        self.submit_order(extra_tp)
                        extra_tp_id = str(extra_tp.client_order_id)
                        layer['tp_order_id'] = extra_tp_id
                        layer['tp_price'] = new_price
                        self._order_to_layer[extra_tp_id] = layer_id
                    except Exception as e:
                        self.log.warning(f"⚠️ Failed to submit per-layer TP for {layer_id}: {e}")
                        layer['tp_order_id'] = new_tp_id
                        layer['tp_price'] = new_price
                        self._order_to_layer[new_tp_id] = layer_id
            if self._layer_orders:
                self._update_aggregate_sltp_state()
                self._persist_layer_orders()
                self.log.info(
                    f"📊 Updated {len(self._layer_orders)} layers with new TP "
                    f"@ ${new_price:,.2f}"
                )

            # v5.13: Mark SL/TP as modified — prevent orphan cascade from async cancel
            self._sltp_modified_this_cycle = True

            self.log.info(f"✅ TP modified via Telegram: ${new_price:,.2f}")

            return {
                'success': True,
                'message': f"✅ *止盈已修改*\n\n"
                          f"🎯 新止盈: ${new_price:,.2f}\n"
                          f"{'已替换' if tp_cancelled else '⚠️ 未找到旧TP，已创建新TP'}\n\n"
                          f"仓位: {self.telegram_bot.side_to_cn(side, 'position')} {quantity:.4f} BTC (${quantity * entry_price:,.0f})\n"
                          f"入场价: ${entry_price:,.2f}"
            }
        except Exception as e:
            self.log.error(f"Error modifying TP: {e}")
            return {'success': False, 'error': str(e)}

    def _cmd_profit(self) -> Dict[str, Any]:
        """Handle /profit command - show P&L analytics from Binance."""
        try:
            if not self.binance_account:
                return {'success': False, 'error': 'Binance 账户未连接'}

            msg = "💹 *盈亏分析*\n"
            msg += "━━━━━━━━━━━━━━━━━━\n"

            # Current position P&L
            pos_data = self._get_current_position_data(from_telegram=True)
            if pos_data:
                side = pos_data['side'].upper()
                pnl = pos_data.get('unrealized_pnl', 0)
                pnl_icon = '🟢' if pnl >= 0 else '🔴'
                side_cn = self.telegram_bot.side_to_cn(side, 'position')
                msg += f"\n📊 *当前持仓*\n"
                msg += f"  {side_cn}: {pnl_icon} ${pnl:,.2f}\n"
            else:
                msg += f"\n📊 *当前持仓*: 无\n"

            # Recent realized P&L
            try:
                realized = self.binance_account.get_income_history(income_type='REALIZED_PNL', limit=20)
                if realized:
                    total_realized = sum(float(r.get('income', 0)) for r in realized)
                    wins = sum(1 for r in realized if float(r.get('income', 0)) > 0)
                    losses = sum(1 for r in realized if float(r.get('income', 0)) < 0)
                    total_trades = wins + losses
                    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
                    r_icon = '🟢' if total_realized >= 0 else '🔴'

                    msg += f"\n📈 *已实现盈亏* (近 {len(realized)} 笔)\n"
                    msg += f"  合计: {r_icon} ${total_realized:,.2f}\n"
                    msg += f"  盈利: {wins} | 亏损: {losses}\n"
                    msg += f"  胜率: {win_rate:.0f}%\n"
            except Exception as e:
                self.log.debug(f"Failed to fetch realized PnL: {e}")

            # Funding fees
            try:
                funding = self.binance_account.get_income_history(income_type='FUNDING_FEE', limit=20)
                if funding:
                    total_funding = sum(float(f.get('income', 0)) for f in funding)
                    f_icon = '🟢' if total_funding >= 0 else '🔴'
                    msg += f"\n💰 *资金费用* (近 {len(funding)} 笔)\n"
                    msg += f"  合计: {f_icon} ${total_funding:,.2f}\n"
            except Exception as e:
                self.log.debug(f"Failed to fetch funding fees: {e}")

            # Commission
            try:
                commission = self.binance_account.get_income_history(income_type='COMMISSION', limit=20)
                if commission:
                    total_comm = sum(float(c.get('income', 0)) for c in commission)
                    msg += f"\n🏷️ *Commissions* (last {len(commission)})\n"
                    msg += f"  Total: ${total_comm:,.2f}\n"
            except Exception as e:
                self.log.debug(f"Failed to fetch commissions: {e}")

            # Balance summary
            try:
                balance = self.binance_account.get_balance()
                if balance and not balance.get('error'):
                    msg += f"\n💳 *Balance*\n"
                    msg += f"  Total: ${balance.get('total_balance', 0):,.2f}\n"
                    msg += f"  Available: ${balance.get('available_balance', 0):,.2f}\n"
                    msg += f"  Unrealized: ${balance.get('unrealized_pnl', 0):,.2f}\n"
            except Exception as e:
                self.log.debug(f"Failed to fetch balance: {e}")

            return {'success': True, 'message': msg}
        except Exception as e:
            self.log.warning(f"Command failed: {e}")
            return {'success': False, 'error': str(e)}

    def _cmd_layer3(self) -> Dict[str, Any]:
        """Handle /layer3 command — removed in mechanical mode (v46.0+).

        AI quality analysis requires AI-era fields (ai_quality_score, entry_timing_verdict)
        that are never written in mechanical mode.
        """
        return {
            'success': True,
            'message': '📊 *Layer 3 结果反馈*\n\n⚠️ Mechanical 模式不支持 AI 质量分析',
        }

    def _cmd_baseline(self) -> Dict[str, Any]:
        """Handle /baseline command - show v44.0 baseline vs current KPIs."""
        try:
            import json
            from pathlib import Path

            baseline_path = Path(__file__).parent.parent / 'data' / 'baseline_v44.json'
            if not baseline_path.exists():
                return {
                    'success': True,
                    'message': (
                        "📊 *基线对比*\n\n"
                        "⚠️ 基线文件不存在\n"
                        "运行 `python3 scripts/measure_baseline.py` 生成基线\n"
                        "需要 >= 100 条交易记录"
                    ),
                }

            with open(baseline_path) as f:
                baseline = json.load(f)

            kpis = baseline.get('kpis', {})
            mc = baseline.get('monte_carlo', {})
            dr = baseline.get('date_range', {})

            msg = "📊 *v44.0 Performance Baseline*\n"
            msg += "━━━━━━━━━━━━━━━━━━\n"
            msg += f"交易数: {baseline.get('sample_size', '?')} | "
            msg += f"周期: {dr.get('period_days', '?')} 天\n"
            msg += f"生成: {baseline.get('generated_at', '?')[:10]}\n"

            msg += "\n*核心 KPI*\n"
            da = kpis.get('direction_accuracy')
            msg += f"  方向准确率: {da:.1%}\n" if da is not None else "  方向准确率: N/A\n"
            avg_rr = kpis.get('avg_rr')
            msg += f"  平均 R/R: {avg_rr:.2f}\n" if avg_rr is not None else "  平均 R/R: N/A\n"
            ga = kpis.get('grade_a_pct')
            msg += f"  A+/A 比例: {ga:.1%}\n" if ga is not None else "  A+/A 比例: N/A\n"
            gf = kpis.get('grade_f_pct')
            msg += f"  F 比例: {gf:.1%}\n" if gf is not None else "  F 比例: N/A\n"

            msg += "\n*风险指标*\n"
            sharpe = kpis.get('sharpe')
            msg += f"  Sharpe: {sharpe:.2f}\n" if sharpe is not None else "  Sharpe: N/A\n"
            max_dd = kpis.get('max_dd')
            msg += f"  Max DD: {max_dd:.1%}\n" if max_dd is not None else "  Max DD: N/A\n"
            calmar = kpis.get('calmar')
            msg += f"  Calmar: {calmar:.2f}\n" if calmar is not None else "  Calmar: N/A\n"

            msg += "\n*信号质量*\n"
            ic = kpis.get('ic_4h')
            msg += f"  IC (4H): {ic:.4f}\n" if ic is not None else "  IC (4H): N/A\n"

            msg += "\n*统计显著性*\n"
            p_val = mc.get('p_value')
            sig = mc.get('alpha_significant', False)
            msg += f"  Monte Carlo p={p_val:.4f}\n" if p_val is not None else "  Monte Carlo: N/A\n"
            msg += f"  Alpha: {'✅ 显著' if sig else '❌ 不显著'}\n"

            # Current rolling KPIs comparison
            memory_file = Path(__file__).parent.parent / 'data' / 'trading_memory.json'
            current_trades = []
            if memory_file.exists():
                try:
                    with open(memory_file) as mf:
                        memories = json.load(mf)
                    current_trades = [m for m in memories
                                      if m.get('evaluation') and 'direction_correct' in m['evaluation']]
                except Exception:
                    pass
            if len(current_trades) >= 5:
                curr_wins = sum(1 for m in current_trades if m['evaluation']['direction_correct'])
                curr_wr = curr_wins / len(current_trades)
                if da is not None:
                    delta = curr_wr - da
                    arrow = '↑' if delta > 0 else '↓' if delta < 0 else '→'
                    msg += f"\n*当前 vs 基线*\n"
                    msg += f"  胜率: {da:.1%} → {curr_wr:.1%} ({arrow}{abs(delta):.1%})\n"

            return {'success': True, 'message': msg}
        except Exception as e:
            self.log.warning(f"Baseline command failed: {e}")
            return {'success': False, 'error': str(e)}

    def _cmd_regime_status(self) -> Dict[str, Any]:
        """Handle /regime command - show HMM regime, Kelly sizing, and risk thresholds."""
        try:
            msg = "🔬 *体制 & 仓位状态*\n"
            msg += "━━━━━━━━━━━━━━━━━━\n"

            # --- HMM Regime ---
            regime = getattr(self, '_current_regime', 'RANGING')
            regime_result = getattr(self, '_last_regime_result', None)
            source = 'adx_fallback'
            confidence = 1.0
            if regime_result:
                source = regime_result.get('source', 'adx_fallback')
                confidence = regime_result.get('confidence', 1.0)

            regime_emoji = {
                'TRENDING_UP': '📈', 'TRENDING_DOWN': '📉',
                'RANGING': '↔️', 'HIGH_VOLATILITY': '🌊',
                'STRONG_TREND': '💪', 'WEAK_TREND': '🔄',
            }.get(regime, '⚪')

            msg += f"\n*市场体制*\n"
            msg += f"  {regime_emoji} 体制: {regime}\n"
            msg += f"  来源: {source}\n"
            msg += f"  置信度: {confidence:.1%}\n"

            # HMM probabilities (if available)
            if regime_result and regime_result.get('probabilities'):
                probs = regime_result['probabilities']
                msg += "  概率分布:\n"
                for state, prob in sorted(probs.items(), key=lambda x: -x[1]):
                    bar = '█' * int(prob * 10) + '░' * (10 - int(prob * 10))
                    msg += f"    {state}: {bar} {prob:.1%}\n"

            # --- Risk Thresholds (regime-adaptive) ---
            risk_summary = self.risk_controller.get_risk_summary()
            thresholds = risk_summary.get('effective_thresholds', {})
            msg += f"\n*风控阈值 (体制自适应)*\n"
            msg += f"  回撤降仓: {thresholds.get('dd_reduce_pct', 0):.1f}%\n"
            msg += f"  回撤熔断: {thresholds.get('dd_halt_pct', 0):.1f}%\n"
            msg += f"  日损上限: {thresholds.get('daily_loss_pct', 0):.1f}%\n"
            msg += f"  最大连亏: {thresholds.get('max_consecutive_losses', 0)}次\n"

            # --- VaR / CVaR ---
            var_95 = risk_summary.get('var_95', 0)
            cvar_95 = risk_summary.get('cvar_95', 0)
            trade_count = risk_summary.get('trade_count', 0)
            if trade_count > 0:
                msg += f"\n*VaR / CVaR (95%)*\n"
                msg += f"  VaR: {var_95:.2f}%\n"
                msg += f"  CVaR: {cvar_95:.2f}%\n"
                msg += f"  样本: {trade_count} 笔交易\n"
            else:
                msg += f"\n*VaR / CVaR*: 数据不足\n"

            # --- Kelly Sizing ---
            kelly = getattr(self, '_kelly_sizer', None)
            msg += f"\n*Kelly 仓位管理*\n"
            if kelly and getattr(kelly, '_enabled', False):
                if kelly._stats is None:
                    kelly._load_stats()
                total_trades = kelly._trade_count
                min_trades = kelly._min_trades

                if total_trades < min_trades:
                    msg += f"  状态: 预热期 ({total_trades}/{min_trades} 笔交易)\n"
                    msg += f"  方法: 固定映射 (HIGH={kelly._FIXED_MAPPING['HIGH']}%/"
                    msg += f"MED={kelly._FIXED_MAPPING['MEDIUM']}%/"
                    msg += f"LOW={kelly._FIXED_MAPPING['LOW']}%)\n"
                else:
                    msg += f"  状态: Kelly 启用\n"
                    msg += f"  分数: f*×{kelly._fraction}\n"
                    for conf in ['HIGH', 'MEDIUM', 'LOW']:
                        stats = kelly._stats.get(conf, {})
                        if stats.get('count', 0) > 0:
                            wr = stats['win_rate']
                            avg_rr = stats['avg_win_rr']
                            msg += f"  {conf}: 胜率{wr:.0%} 均R/R{avg_rr:.2f} ({stats['count']}笔)\n"
                msg += f"  总交易: {total_trades} 笔\n"
            else:
                msg += f"  状态: 未启用 (固定映射)\n"

            # --- Fear & Greed ---
            fg_data = getattr(self, '_cached_fear_greed', None)
            if fg_data:
                fg_val = fg_data.get('value', 50)
                fg_class = fg_data.get('classification', 'Neutral')
                fg_emoji = '😱' if fg_val < 20 else '😰' if fg_val < 40 else '😐' if fg_val < 60 else '😊' if fg_val < 80 else '🤑'
                extreme_warn = " ⚠️" if fg_data.get('is_extreme', False) else ""
                msg += f"\n*恐贪指数*\n"
                msg += f"  {fg_emoji} {fg_val} ({fg_class}){extreme_warn}\n"

            return {'success': True, 'message': msg}
        except Exception as e:
            self.log.warning(f"Regime command failed: {e}")
            return {'success': False, 'error': str(e)}

    def _cmd_reload_config(self) -> Dict[str, Any]:
        """Handle /reload_config command - reload YAML config without restart."""
        try:
            from utils.config_manager import ConfigManager

            # Reload configuration
            config_mgr = ConfigManager(env=getattr(self, '_config_env', 'production'))
            config_mgr.load()

            # Update key runtime parameters
            updated = []

            # Trading logic params
            new_min_rr = config_mgr.get('trading_logic', 'min_rr_ratio', default=1.5)
            if hasattr(self, 'min_rr_ratio') and self.min_rr_ratio != new_min_rr:
                self.min_rr_ratio = new_min_rr
                updated.append(f"min_rr_ratio: {new_min_rr}")

            # Position sizing
            new_max_pos = config_mgr.get('position', 'max_position_ratio', default=0.12)
            if hasattr(self, 'max_position_ratio') and self.max_position_ratio != new_max_pos:
                self.max_position_ratio = new_max_pos
                updated.append(f"max_position_ratio: {new_max_pos}")

            # Risk params
            new_min_conf = config_mgr.get('risk', 'min_confidence_to_trade', default='LOW')
            if hasattr(self, 'min_confidence_to_trade') and self.min_confidence_to_trade != new_min_conf:
                self.min_confidence_to_trade = new_min_conf
                updated.append(f"min_confidence: {new_min_conf}")

            self.log.info(f"⚙️ Config reloaded via Telegram, {len(updated)} params updated")

            if updated:
                changes = "\n".join(f"  - {u}" for u in updated)
                msg = f"✅ *配置已重载*\n\n更新参数:\n{changes}"
            else:
                msg = "✅ *配置已重载*\n\n所有参数未变化。"

            return {'success': True, 'message': msg}
        except Exception as e:
            self.log.error(f"Error reloading config: {e}")
            return {'success': False, 'error': str(e)}

    def _cmd_calibrate(self) -> Dict[str, Any]:
        """Handle /calibrate command - trigger S/R hold probability calibration."""
        from pathlib import Path

        try:
            self.log.info("Starting S/R calibration via Telegram command...")

            # Send immediate acknowledgment
            if self.telegram_bot and self.enable_telegram:
                self.telegram_bot.send_message_sync(
                    f"⏳ *S/R 校准启动中*\n\n"
                    f"正在获取 30 天历史数据并计算校准因子...\n"
                    f"预计需要 2-3 分钟，完成后会通知。\n\n"
                    f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC",
                    use_queue=False,
                )

            def _run_calibration():
                try:
                    result = subprocess.run(
                        [sys.executable, "scripts/calibrate_hold_probability.py",
                         "--auto-calibrate", "--no-telegram", "--days", "30"],
                        capture_output=True, text=True, timeout=600,
                        cwd=str(Path(__file__).parent.parent),
                    )
                    if result.returncode == 0:
                        # Force reload calibration cache and report
                        try:
                            from utils.calibration_loader import (
                                load_calibration, get_calibration_summary,
                            )
                            load_calibration(force_reload=True)
                            summary = get_calibration_summary()
                            if self.telegram_bot and self.enable_telegram:
                                self.telegram_bot.send_message_sync(
                                    f"✅ *S/R 校准完成*\n\n"
                                    f"版本: `{summary['version']}`\n"
                                    f"样本: {summary['sample_count']} zones\n"
                                    f"Hold rate: {summary['overall_hold_rate'] * 100:.1f}%\n\n"
                                    f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC",
                                    use_queue=False,
                                )
                        except Exception as tg_err:
                            self.log.debug(f"Telegram notify failed in calibrate success: {tg_err}")
                        self.log.info("S/R calibration completed successfully")
                    else:
                        error = result.stderr[-500:] if result.stderr else "Unknown error"
                        self.log.error(f"S/R calibration failed: {error}")
                        if self.telegram_bot and self.enable_telegram:
                            try:
                                self.telegram_bot.send_message_sync(
                                    f"❌ *S/R 校准失败*\n\n{error}\n\n"
                                    f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC",
                                    use_queue=False,
                                )
                            except Exception as tg_err:
                                self.log.debug(f"Telegram notify failed in calibrate failure: {tg_err}")
                except subprocess.TimeoutExpired:
                    self.log.error("S/R calibration timed out (10 min)")
                    if self.telegram_bot and self.enable_telegram:
                        try:
                            self.telegram_bot.send_message_sync(
                                f"❌ *S/R 校准超时* (10分钟限制)\n\n"
                                f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC",
                                use_queue=False,
                            )
                        except Exception as tg_err:
                            self.log.debug(f"Telegram notify failed in calibrate timeout: {tg_err}")
                except Exception as e:
                    self.log.error(f"S/R calibration error: {e}")

            # Run in background thread to not block strategy
            thread = threading.Thread(target=_run_calibration, daemon=True)
            thread.start()

            return {
                'success': True,
                'message': "⏳ *S/R 校准已启动*\n\n后台运行中，完成后通知。",
            }
        except Exception as e:
            self.log.error(f"Error starting calibration: {e}")
            return {'success': False, 'error': str(e)}

    # ===== Equity Snapshot Persistence =====

    _EQUITY_SNAPSHOT_FILE = "data/equity_snapshots.json"

    def _load_equity_snapshots(self):
        """Load persisted equity snapshots from disk (survives restarts)."""
        from pathlib import Path
        try:
            path = Path(self._EQUITY_SNAPSHOT_FILE)
            if path.exists():
                with open(path, 'r') as f:
                    data = json.load(f)
                self._daily_starting_equity = data.get('daily_starting_equity', 0.0)
                self._weekly_starting_equity = data.get('weekly_starting_equity', 0.0)
                self._last_equity_date = data.get('last_equity_date', '')
                self._last_equity_week = data.get('last_equity_week', '')
                self._signals_generated_today = data.get('signals_generated_today', 0)
                self._signals_executed_today = data.get('signals_executed_today', 0)
                self.log.info(
                    f"📊 Loaded equity snapshots: daily=${self._daily_starting_equity:,.2f} "
                    f"({self._last_equity_date}), weekly=${self._weekly_starting_equity:,.2f} "
                    f"({self._last_equity_week})"
                )
        except Exception as e:
            self.log.debug(f"Equity snapshots load skipped: {e}")

    def _save_equity_snapshots(self):
        """Persist equity snapshots to disk."""
        from pathlib import Path
        try:
            path = Path(self._EQUITY_SNAPSHOT_FILE)
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                'daily_starting_equity': self._daily_starting_equity,
                'weekly_starting_equity': self._weekly_starting_equity,
                'last_equity_date': self._last_equity_date,
                'last_equity_week': self._last_equity_week,
                'signals_generated_today': self._signals_generated_today,
                'signals_executed_today': self._signals_executed_today,
            }
            with open(path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            self.log.debug(f"Equity snapshots save skipped: {e}")

    def _check_scheduled_summaries(self):
        """
        Check if daily/weekly summaries need to be sent (v3.13).

        Called from on_timer. Checks current UTC time against configured schedule.
        Uses date tracking to avoid duplicate sends.
        """
        if not self.telegram_bot or not self.enable_telegram:
            return

        try:

            now = datetime.now(timezone.utc)
            current_hour = now.hour
            current_weekday = now.weekday()  # 0=Monday, 6=Sunday
            today_str = now.strftime('%Y-%m-%d')
            week_str = now.strftime('%Y-W%W')  # Year-Week format

            # Check daily summary
            if getattr(self, 'telegram_auto_daily', False):
                daily_hour = getattr(self, 'telegram_daily_hour_utc', 0)

                # Send at the configured hour, once per day
                if current_hour == daily_hour:
                    last_daily = getattr(self, '_last_daily_summary_date', None)
                    if last_daily != today_str:
                        self.log.info(f"📊 Sending scheduled daily summary...")
                        # Report on yesterday (the day that just ended), not today
                        yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')
                        result = self._cmd_daily_summary(report_date=yesterday)
                        if result.get('success') and result.get('message'):
                            self.telegram_bot.send_message_sync(result['message'], broadcast=True)
                            self._last_daily_summary_date = today_str
                            self.log.info("✅ Daily summary sent")
                        else:
                            self.log.warning(f"Failed to generate daily summary: {result.get('error')}")

            # Check weekly summary
            if getattr(self, 'telegram_auto_weekly', False):
                weekly_day = getattr(self, 'telegram_weekly_day', 0)  # 0=Monday
                daily_hour = getattr(self, 'telegram_daily_hour_utc', 0)

                # Send on the configured day at the configured hour
                if current_weekday == weekly_day and current_hour == daily_hour:
                    last_weekly = getattr(self, '_last_weekly_summary_date', None)
                    if last_weekly != week_str:
                        self.log.info(f"📊 Sending scheduled weekly summary...")
                        # Report on last week (pass yesterday = Sunday to anchor to previous week)
                        yesterday = now - timedelta(days=1)
                        result = self._cmd_weekly_summary(report_date=yesterday)
                        if result.get('success') and result.get('message'):
                            self.telegram_bot.send_message_sync(result['message'], broadcast=True)
                            self._last_weekly_summary_date = week_str
                            self.log.info("✅ Weekly summary sent")
                        else:
                            self.log.warning(f"Failed to generate weekly summary: {result.get('error')}")

        except Exception as e:
            self.log.warning(f"Error checking scheduled summaries: {e}")
