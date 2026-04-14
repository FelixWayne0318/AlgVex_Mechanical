"""
SRP v6.1 NautilusTrader Strategy — Thin Shell

This file ONLY handles:
  - NT lifecycle (on_start, on_stop)
  - Bar routing (on_bar → signal_engine.on_bar)
  - Order execution (signal → submit_order)
  - Event callbacks (on_order_filled, on_position_closed)
  - State persistence (save/load JSON)
  - Telegram notifications

ALL trading logic lives in signal_engine.py.
"""

import os
import json
import logging
from typing import Optional
from datetime import timedelta

import yaml

from nautilus_trader.config import StrategyConfig
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce, TriggerType
from nautilus_trader.model.identifiers import InstrumentId, ClientOrderId
from nautilus_trader.model.instruments import Instrument

from srp.strategy.signal_engine import SRPSignalEngine, SRPConfig, SRPSignal

# Reuse project's mature Telegram component (async queue, retry, rate limit)
try:
    from utils.telegram_bot import TelegramBot
except ImportError:
    TelegramBot = None  # Graceful fallback for standalone/test usage


# =============================================================================
# Configuration
# =============================================================================

class SRPStrategyConfig(StrategyConfig, frozen=True):
    """NT Strategy config — instrument + timing only.

    Trading logic params are in srp/configs/srp.yaml → SRPConfig.
    """
    instrument_id: str = "BTCUSDT-PERP.BINANCE"
    bar_type: str = "BTCUSDT-PERP.BINANCE-30-MINUTE-LAST-EXTERNAL"
    config_path: str = "srp/configs/srp.yaml"
    timer_interval_sec: int = 1200
    enable_telegram: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


def load_srp_config(path: str) -> SRPConfig:
    """Load SRPConfig from YAML file."""
    if not os.path.exists(path):
        logging.warning(f"SRP config not found: {path}, using defaults")
        return SRPConfig()
    with open(path) as f:
        raw = yaml.safe_load(f)
    return SRPConfig(
        src_type=raw.get("src_type", "HLC3"),
        vwma_length=raw.get("vwma_length", 14),
        srp_pct=raw.get("srp_pct", 1.0),
        rsi_period=raw.get("rsi_period", 7),
        mfi_period=raw.get("mfi_period", 7),
        rsi_mfi_below=raw.get("rsi_mfi_below", 55.0),
        rsi_mfi_above=raw.get("rsi_mfi_above", 95.0),
        base_order_pct=raw.get("base_order_pct", 10.0) / 100.0,
        mintp=raw.get("mintp", 0.025),
        mintp_protection=raw.get("mintp_protection", True),
        max_dca_count=raw.get("max_dca_count", 4),
        dca_multiplier=raw.get("dca_multiplier", 1.5),
        dca_min_change_pct=raw.get("dca_min_change_pct", 3.0),
        dca_type=raw.get("dca_type", "volume_multiply"),
        vdca_enabled=raw.get("vdca_enabled", True),
        vdca_max=raw.get("vdca_max", 10),
        max_loss_pct=raw.get("max_loss_pct", 0.06),
        cycle_enabled=raw.get("cycle_enabled", True),
        cycle_dates=raw.get("cycle_dates", SRPConfig().cycle_dates),
        commission_pct=raw.get("commission_pct", 0.00075),
    )


# =============================================================================
# Strategy
# =============================================================================

class SRPStrategy(Strategy):
    """SRP v6.1 — VWMA + RSI-MFI Mean Reversion with DCA (Long + Short).

    This is a thin NT shell. All signal logic is in SRPSignalEngine.
    """

    def __init__(self, config: SRPStrategyConfig):
        super().__init__(config)

        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        self.instrument: Optional[Instrument] = None

        # Load signal engine
        self._srp_config = load_srp_config(config.config_path)
        self.engine = SRPSignalEngine(self._srp_config)

        # Server-side SL tracking
        self._sl_order_id: Optional[str] = None

        # Pending close tracking (NT async: close order → wait for event)
        self._pending_close: bool = False

        # Telegram — reuse project's TelegramBot (async queue, retry, rate limit)
        self._telegram: Optional[object] = None
        if TelegramBot and config.enable_telegram and config.telegram_bot_token:
            self._telegram = TelegramBot(
                token=config.telegram_bot_token,
                chat_id=config.telegram_chat_id,
                enabled=True,
                use_queue=True,
                queue_db_path=os.path.join(
                    os.path.dirname(os.path.dirname(__file__)), "data", "srp_telegram_queue.db"
                ),
            )

        # Persistence
        self._data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
        self._state_file = os.path.join(self._data_dir, "srp_state.json")

    # =========================================================================
    # Lifecycle
    # =========================================================================

    def on_start(self):
        self.log.info("SRP v6.1 starting...")
        self.instrument = self.cache.instrument(self.instrument_id)
        if self.instrument is None:
            self.log.error(f"Instrument {self.instrument_id} not found")
            return

        # Pre-fetch historical bars for live mode
        if not self._is_backtest():
            self._prefetch_bars()
            self._load_state()
            self._reconcile_position()

        self.subscribe_bars(self.bar_type)

        self.clock.set_timer(
            name="srp_heartbeat",
            interval=timedelta(seconds=self.config.timer_interval_sec),
            callback=self._on_heartbeat,
        )

        cycle_status = "cycle ON" if self._srp_config.cycle_enabled else "long only"
        self.log.info(
            f"SRP v6.1 ready ({cycle_status}) "
            f"SRP={self._srp_config.srp_pct}% DCA={self._srp_config.max_dca_count}x "
            f"TP={self._srp_config.mintp*100:.1f}% SL={self._srp_config.max_loss_pct*100:.0f}%"
        )
        self._send_telegram(
            f"🟢 SRP v6.1 started ({cycle_status})\n"
            f"SRP: {self._srp_config.srp_pct}% VWMA={self._srp_config.vwma_length}\n"
            f"DCA: {self._srp_config.max_dca_count}x spacing={self._srp_config.dca_min_change_pct}%\n"
            f"TP: {self._srp_config.mintp*100:.1f}% SL: {self._srp_config.max_loss_pct*100:.0f}%"
        )

    def on_stop(self):
        self._save_state()
        self._send_telegram("🛑 SRP v6.1 stopped")

    # =========================================================================
    # Bar Processing — delegates entirely to signal engine
    # =========================================================================

    def on_bar(self, bar: Bar):
        if self.instrument is None:
            return
        if self._pending_close:
            return  # Wait for close confirmation before processing

        equity = self._get_equity()
        signal = self.engine.on_bar(
            o=float(bar.open),
            h=float(bar.high),
            l=float(bar.low),
            c=float(bar.close),
            v=float(bar.volume),
            equity=equity,
            ts_ms=int(bar.ts_event / 1_000_000),  # ns → ms
        )

        self._execute_signal(signal)

    # =========================================================================
    # Signal Execution
    # =========================================================================

    def _execute_signal(self, signal: SRPSignal):
        if signal.action == "HOLD":
            return

        if signal.action.startswith("EXIT"):
            self._submit_close(signal)
        elif signal.action.startswith("ENTRY") or signal.action.startswith("DCA"):
            self._submit_entry(signal)

    def _submit_entry(self, signal: SRPSignal):
        side = OrderSide.BUY if "LONG" in signal.action else OrderSide.SELL
        qty = signal.quantity

        rounded_qty = float(self.instrument.make_qty(qty))
        min_qty = float(self.instrument.min_quantity) if self.instrument.min_quantity else 0.001
        if rounded_qty < min_qty:
            self.log.warning(f"SRP {signal.reason}: qty {qty:.6f} < min {min_qty}")
            return

        try:
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=side,
                quantity=self.instrument.make_qty(qty),
                time_in_force=TimeInForce.GTC,
                reduce_only=False,
            )
            self.submit_order(order)
            self.log.info(f"SRP {signal.action}: {signal.reason} qty={qty:.6f} @ ${signal.price:,.2f}")
        except Exception as e:
            self.log.error(f"SRP entry failed: {e}")

    def _submit_close(self, signal: SRPSignal):
        # Get actual position quantity from NT cache
        sell_qty = self._get_position_qty()
        if sell_qty <= 0:
            self.log.warning(f"SRP close: no position to close ({signal.reason})")
            return

        # Close side is opposite of deal direction
        close_side = OrderSide.SELL if signal.deal_dir == 1 else OrderSide.BUY

        try:
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=close_side,
                quantity=self.instrument.make_qty(sell_qty),
                time_in_force=TimeInForce.GTC,
                reduce_only=True,
            )
            self.submit_order(order)
            self._pending_close = True
            self.log.info(f"SRP {signal.action}: {signal.reason} @ ${signal.price:,.2f}")

            self._send_telegram(
                f"📊 SRP 平仓 [{signal.reason}]\n"
                f"价格: ${signal.price:,.2f}\n"
                f"均价: ${self.engine.state.avg_price:,.2f}\n"
                f"虚拟DCA: {signal.v_count}次"
            )
        except Exception as e:
            self._pending_close = False
            self.log.error(f"SRP close failed: {e}")

    # =========================================================================
    # NT Event Callbacks — state updated here, not at submission
    # =========================================================================

    def on_order_filled(self, event):
        fill_price = float(event.last_px)
        fill_qty = float(event.last_qty)
        filled_order = self.cache.order(event.client_order_id)

        if filled_order and filled_order.is_reduce_only:
            # Close fill — state will be reset in on_position_closed
            self.log.info(f"SRP close filled @ ${fill_price:,.2f}")
        else:
            # Entry fill — update signal engine state
            self.engine.update_fill(fill_price, fill_qty, is_close=False)
            self._save_state()
            self._update_server_sl()

            self._send_telegram(
                f"📈 SRP 入场确认\n"
                f"成交价: ${fill_price:,.2f}\n"
                f"数量: {fill_qty:.6f}\n"
                f"均价: ${self.engine.state.avg_price:,.2f}\n"
                f"DCA: {self.engine.state.opentrades}/{self._srp_config.sonum}"
            )

    def on_position_closed(self, event):
        self.log.info(f"SRP position closed confirmed: {self.engine.state.last_exit_type}")
        self.engine.update_fill(0, 0, is_close=True)
        self._pending_close = False
        self._cancel_server_sl()
        self._save_state()

    def on_order_rejected(self, event):
        self.log.warning(f"SRP order rejected: {event}")
        if self._pending_close:
            self.log.error("SRP close rejected! Clearing pending state.")
            self._pending_close = False

    # =========================================================================
    # Helpers
    # =========================================================================

    def _is_backtest(self) -> bool:
        try:
            return "TestClock" in type(self.clock).__name__
        except Exception:
            return False

    def _get_equity(self) -> float:
        try:
            account = self.portfolio.account(self.instrument_id.venue)
            if account:
                for currency, balance in account.balances().items():
                    if str(currency) == "USDT":
                        return float(balance.total)
        except Exception:
            pass
        return 1500.0  # Fallback to initial_capital

    def _get_position_qty(self) -> float:
        try:
            for pos in self.cache.positions(venue=self.instrument_id.venue):
                if pos.instrument_id == self.instrument_id and not pos.is_closed:
                    return float(pos.quantity)
        except Exception:
            pass
        return self.engine.state.total_qty

    # =========================================================================
    # Server-Side SL (exchange-level crash protection)
    # =========================================================================

    def _update_server_sl(self):
        if self._is_backtest() or self.instrument is None:
            return
        s = self.engine.state
        if s.deal_dir == 0 or s.avg_price <= 0:
            return
        self._cancel_server_sl()
        if s.deal_dir == -1:
            sl_price = s.avg_price * (1.0 + self._srp_config.max_loss_pct)
            sl_side = OrderSide.BUY
        else:
            sl_price = s.avg_price * (1.0 - self._srp_config.max_loss_pct)
            sl_side = OrderSide.SELL
        try:
            qty = self._get_position_qty()
            order = self.order_factory.stop_market(
                instrument_id=self.instrument_id,
                order_side=sl_side,
                quantity=self.instrument.make_qty(qty),
                trigger_price=self.instrument.make_price(sl_price),
                trigger_type=TriggerType.LAST_PRICE,
                reduce_only=True,
            )
            self.submit_order(order)
            self._sl_order_id = str(order.client_order_id)
        except Exception as e:
            self.log.error(f"Server SL failed: {e}")

    def _cancel_server_sl(self):
        if self._sl_order_id is None:
            return
        try:
            order = self.cache.order(ClientOrderId(self._sl_order_id))
            if order and order.is_open:
                self.cancel_order(order)
        except Exception:
            pass
        self._sl_order_id = None

    # =========================================================================
    # Position Reconciliation (startup)
    # =========================================================================

    def _reconcile_position(self):
        try:
            exchange_qty = 0.0
            for pos in self.cache.positions(venue=self.instrument_id.venue):
                if pos.instrument_id == self.instrument_id and not pos.is_closed:
                    exchange_qty = float(pos.quantity)
                    break
            s = self.engine.state
            state_has = s.deal_dir != 0 and s.total_qty > 0
            exchange_has = exchange_qty > 0
            if state_has and not exchange_has:
                self.log.warning("Reconcile: state has position, exchange flat — resetting")
                self.engine.reset_state()
            elif not state_has and exchange_has:
                self.log.warning(f"Reconcile: state flat, exchange has qty={exchange_qty:.6f} — adopting")
                s.deal_dir = 1
                s.total_qty = exchange_qty
                s.opentrades = 1
                self._update_server_sl()
            elif state_has and exchange_has:
                if abs(s.total_qty - exchange_qty) > 0.0001:
                    self.log.warning(f"Reconcile: qty mismatch state={s.total_qty:.6f} exchange={exchange_qty:.6f}")
                    s.total_qty = exchange_qty
                    s.total_cost = s.total_qty * s.avg_price
                self._update_server_sl()
            self.log.info("Reconciliation complete")
        except Exception as e:
            self.log.error(f"Reconciliation failed: {e}")

    # =========================================================================
    # Historical Bar Prefetch
    # =========================================================================

    def _prefetch_bars(self, limit: int = 500):
        """Pre-fetch historical bars from Binance to fill indicator buffers.

        Reuses project's BinanceKlineClient (retry, rate limit, error handling).
        """
        try:
            from utils.binance_kline_client import BinanceKlineClient
            client = BinanceKlineClient()
            symbol = str(self.instrument_id).split('-')[0]
            klines = client.get_klines(symbol=symbol, interval='30m', limit=min(limit, 1500))
            if not klines:
                self.log.warning("Pre-fetch: no bars returned")
                return
            if len(klines) > 1:
                klines = klines[:-1]  # Strip incomplete bar
            equity = self._get_equity()
            for k in klines:
                self.engine.on_bar(
                    o=float(k[1]), h=float(k[2]), l=float(k[3]),
                    c=float(k[4]), v=float(k[5]),
                    equity=equity,
                    ts_ms=int(k[0]),
                )
            self.log.info(f"Pre-fetched {len(klines)} bars via BinanceKlineClient")
        except ImportError:
            self.log.warning("BinanceKlineClient not available, skipping prefetch")
        except Exception as e:
            self.log.error(f"Pre-fetch failed: {e}")

    # =========================================================================
    # Persistence
    # =========================================================================

    def _save_state(self):
        try:
            os.makedirs(self._data_dir, exist_ok=True)
            state = self.engine.get_state_dict()
            state["sl_order_id"] = self._sl_order_id
            with open(self._state_file, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            self.log.error(f"Save state failed: {e}")

    def _load_state(self):
        if not os.path.exists(self._state_file):
            return
        try:
            with open(self._state_file) as f:
                d = json.load(f)
            self._sl_order_id = d.pop("sl_order_id", None)
            self.engine.load_state_dict(d)
            self.log.info(f"State restored: dir={self.engine.state.deal_dir} "
                         f"qty={self.engine.state.total_qty:.6f}")
        except Exception as e:
            self.log.error(f"Load state failed: {e}")

    # =========================================================================
    # Heartbeat + Telegram
    # =========================================================================

    def _on_heartbeat(self, event):
        s = self.engine.state
        if s.deal_dir == 0:
            return
        if not self.engine._closes:
            return
        price = self.engine._closes[-1]
        if s.avg_price > 0:
            pnl = (s.avg_price - price) / s.avg_price * 100 if s.deal_dir == -1 else (price - s.avg_price) / s.avg_price * 100
            self.log.info(f"SRP heartbeat: ${price:,.1f} avg=${s.avg_price:,.1f} pnl={pnl:+.2f}%")

    def _send_telegram(self, text: str):
        """Send Telegram message via project's TelegramBot (async queue, retry, rate limit).

        Falls back to no-op if TelegramBot unavailable (backtest, standalone test).
        """
        if self._telegram is None:
            return
        try:
            self._telegram.send_message_sync(f"[SRP] {text}")
        except Exception:
            pass  # Non-blocking — telegram failure must not affect trading
