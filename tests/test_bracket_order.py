"""Unit tests for AI Trading Strategy bracket order helpers."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import Mock
import enum
import sys
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _ensure_nautilus_stub() -> None:
    """Create minimal nautilus_trader stubs so strategy module can import."""
    try:
        import nautilus_trader  # type: ignore
        return
    except ModuleNotFoundError:
        pass

    base = types.ModuleType("nautilus_trader")
    sys.modules["nautilus_trader"] = base

    config_mod = types.ModuleType("nautilus_trader.config")
    class StrategyConfig:  # noqa: D401 - minimal stub
        """Stub StrategyConfig."""
        def __init_subclass__(cls, **kwargs: Any) -> None:
            return
    config_mod.StrategyConfig = StrategyConfig
    sys.modules["nautilus_trader.config"] = config_mod

    trading_mod = types.ModuleType("nautilus_trader.trading")
    sys.modules["nautilus_trader.trading"] = trading_mod

    trade_strategy_mod = types.ModuleType("nautilus_trader.trading.strategy")
    class Strategy:
        def __init__(self, config: StrategyConfig | None = None) -> None:
            self.config = config
    trade_strategy_mod.Strategy = Strategy
    sys.modules["nautilus_trader.trading.strategy"] = trade_strategy_mod

    model_mod = types.ModuleType("nautilus_trader.model")
    sys.modules["nautilus_trader.model"] = model_mod

    data_mod = types.ModuleType("nautilus_trader.model.data")
    class Bar:
        def __init__(self, open_price, high, low, close, volume):
            self.open = open_price
            self.high = high
            self.low = low
            self.close = close
            self.volume = volume
    class BarType:
        @classmethod
        def from_str(cls, value: str) -> str:
            return value
    data_mod.Bar = Bar
    data_mod.BarType = BarType
    sys.modules["nautilus_trader.model.data"] = data_mod

    enums_mod = types.ModuleType("nautilus_trader.model.enums")
    OrderSide = enum.Enum("OrderSide", "BUY SELL")
    TimeInForce = enum.Enum("TimeInForce", "GTC FOK IOC")
    PositionSide = enum.Enum("PositionSide", "LONG SHORT")
    PriceType = enum.Enum("PriceType", "LAST MARK")
    TriggerType = enum.Enum("TriggerType", "LAST INDEX MARK DEFAULT")
    OrderType = enum.Enum("OrderType", "MARKET LIMIT STOP_MARKET")
    enums_mod.OrderSide = OrderSide
    enums_mod.TimeInForce = TimeInForce
    enums_mod.PositionSide = PositionSide
    enums_mod.PriceType = PriceType
    enums_mod.TriggerType = TriggerType
    enums_mod.OrderType = OrderType
    sys.modules["nautilus_trader.model.enums"] = enums_mod

    identifiers_mod = types.ModuleType("nautilus_trader.model.identifiers")
    class InstrumentId(str):
        @classmethod
        def from_str(cls, value: str) -> "InstrumentId":
            return cls(value)
    identifiers_mod.InstrumentId = InstrumentId
    sys.modules["nautilus_trader.model.identifiers"] = identifiers_mod

    instruments_mod = types.ModuleType("nautilus_trader.model.instruments")
    class Instrument:
        def make_qty(self, quantity: float) -> Decimal:
            return Decimal(str(quantity))
        def make_price(self, price: float) -> Decimal:
            return Decimal(str(price))
    instruments_mod.Instrument = Instrument
    sys.modules["nautilus_trader.model.instruments"] = instruments_mod

    position_mod = types.ModuleType("nautilus_trader.model.position")
    class Position:
        pass
    position_mod.Position = Position
    sys.modules["nautilus_trader.model.position"] = position_mod

    orders_mod = types.ModuleType("nautilus_trader.model.orders")
    class MarketOrder:
        pass
    orders_mod.MarketOrder = MarketOrder
    sys.modules["nautilus_trader.model.orders"] = orders_mod

    indicators_mod = types.ModuleType("nautilus_trader.indicators")

    class _Indicator:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.value = 0.0
            self.initialized = False

        def update_raw(self, value: float) -> None:
            self.value = value
            self.initialized = True

    class SimpleMovingAverage(_Indicator):
        pass

    class ExponentialMovingAverage(_Indicator):
        pass

    class RelativeStrengthIndex(_Indicator):
        pass

    class MovingAverageConvergenceDivergence(_Indicator):
        pass

    class AverageTrueRange(_Indicator):
        pass

    indicators_mod.SimpleMovingAverage = SimpleMovingAverage
    indicators_mod.ExponentialMovingAverage = ExponentialMovingAverage
    indicators_mod.RelativeStrengthIndex = RelativeStrengthIndex
    indicators_mod.MovingAverageConvergenceDivergence = MovingAverageConvergenceDivergence
    indicators_mod.AverageTrueRange = AverageTrueRange
    sys.modules["nautilus_trader.indicators"] = indicators_mod

    openai_mod = types.ModuleType("openai")

    class OpenAI:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=self._create_response)
            )

        def _create_response(self, *args: Any, **kwargs: Any) -> Any:
            content = (
                '{\"signal\":\"HOLD\",\"confidence\":\"LOW\",\"reason\":\"stub\",'
                '\"stop_loss\":0,\"take_profit\":0}'
            )
            message = types.SimpleNamespace(content=content)
            choice = types.SimpleNamespace(message=message)
            return types.SimpleNamespace(choices=[choice])

    openai_mod.OpenAI = OpenAI
    sys.modules["openai"] = openai_mod


_ensure_nautilus_stub()

from nautilus_trader.model.enums import OrderSide, OrderType  # type: ignore
from strategy.ai_strategy import AITradingStrategy


class DummyInstrument:
    def make_qty(self, quantity: float) -> Decimal:
        return Decimal(str(quantity))

    def make_price(self, price: float) -> Decimal:
        return Decimal(str(price))


class DummyOrderFactory:
    """v4.17: Stub supports limit() (entry), market() (close/reduce), and bracket() (legacy)."""
    def __init__(self) -> None:
        self.limit_kwargs: Dict[str, Any] | None = None
        self.market_kwargs: Dict[str, Any] | None = None
        self.kwargs: Dict[str, Any] | None = None

    def bracket(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        orders = [SimpleNamespace(order_type=OrderType.STOP_MARKET, client_order_id="SL-order")]
        return SimpleNamespace(orders=orders, id="order-list-001")

    def limit(self, **kwargs: Any) -> Any:
        self.limit_kwargs = kwargs
        return SimpleNamespace(client_order_id="entry-limit-001")

    def market(self, **kwargs: Any) -> Any:
        self.market_kwargs = kwargs
        return SimpleNamespace(client_order_id="entry-001")


class DummyCache:
    def __init__(self, bars: List[Any]) -> None:
        self._bars = bars

    def bars(self, bar_type: Any) -> List[Any]:
        return self._bars


class DummyLogger:
    def info(self, *args: Any, **kwargs: Any) -> None:
        pass

    def warning(self, *args: Any, **kwargs: Any) -> None:
        pass

    def error(self, *args: Any, **kwargs: Any) -> None:
        pass

    def debug(self, *args: Any, **kwargs: Any) -> None:
        pass


def _make_strategy_stub() -> AITradingStrategy:
    strategy = AITradingStrategy.__new__(AITradingStrategy)
    strategy.position_config = {
        "min_trade_amount": 0.001,
        "adjustment_threshold": 0.0,
    }
    strategy.enable_auto_sl_tp = True
    strategy.sl_use_support_resistance = True
    strategy.sl_buffer_pct = 0.001
    strategy.tp_pct_config = {"HIGH": 0.03, "MEDIUM": 0.02, "LOW": 0.01}
    strategy.latest_signal_data = {
        "confidence": "HIGH",
        "stop_loss": 950.0,
        "take_profit": 1080.0,
    }
    strategy.latest_technical_data = {"support": 950.0, "resistance": 1050.0}
    strategy.latest_price_data = {"price": 1000.0}
    strategy.indicator_manager = SimpleNamespace(recent_bars=[])
    strategy.cache = DummyCache([])
    strategy.bar_type = "BTC-BARS"
    strategy.order_factory = DummyOrderFactory()
    strategy.submit_order = Mock()
    strategy.submit_order_list = Mock()
    strategy._submit_order = Mock()
    strategy.instrument = DummyInstrument()
    strategy.instrument_id = "BTCUSDT-PERP.BINANCE"
    strategy.sltp_state = {}
    strategy.log = DummyLogger()
    # v4.11+: Additional attributes needed for _validate_sltp_for_entry
    strategy.binance_account = None
    strategy.telegram_bot = None
    strategy.enable_telegram = False
    strategy._last_signal_status = {}
    # v5.0: ATR/S/R attributes
    strategy._cached_atr_value = 50.0
    strategy.min_rr_ratio = 1.5
    strategy.atr_buffer_multiplier = 0.5
    # v4.13: Two-phase pending SL/TP storage
    strategy._pending_sltp = None
    # v4.17: Pending LIMIT entry order tracking
    strategy._pending_entry_order_id = None
    return strategy


def test_submit_bracket_order_stores_pending_sltp() -> None:
    """v4.17: Two-phase approach — LIMIT entry order submitted, SL/TP stored as pending."""
    strategy = _make_strategy_stub()

    strategy._submit_bracket_order(OrderSide.BUY, 0.01)

    # v4.17: Entry order submitted via order_factory.limit() (was market() in v4.13)
    assert strategy.order_factory.limit_kwargs is not None, "LIMIT entry should be submitted"
    assert strategy.order_factory.limit_kwargs["order_side"] == OrderSide.BUY
    assert "price" in strategy.order_factory.limit_kwargs, "LIMIT order must have price"
    assert strategy.order_factory.market_kwargs is None, "MARKET should NOT be used for entry"

    # v4.17: Pending entry order ID tracked for on_timer cancellation
    assert strategy._pending_entry_order_id == "entry-limit-001"

    # v4.13: SL/TP stored in _pending_sltp for on_position_opened()
    assert strategy._pending_sltp is not None, "Pending SL/TP should be stored"
    assert strategy._pending_sltp["sl_price"] > 0, "SL price should be positive"
    assert strategy._pending_sltp["tp_price"] > 0, "TP price should be positive"
    assert strategy._pending_sltp["sl_price"] < 1000.0, "BUY SL should be below entry"
    assert strategy._pending_sltp["tp_price"] > 1000.0, "BUY TP should be above entry"

    # sltp_state should be pre-initialized
    instrument_key = str(strategy.instrument_id)
    assert instrument_key in strategy.sltp_state
    assert strategy.sltp_state[instrument_key]["side"] == "LONG"

    # submit_order_list NOT called (v4.13+ doesn't use bracket anymore)
    strategy.submit_order_list.assert_not_called()


def test_submit_bracket_order_blocks_when_price_missing() -> None:
    """v3.18 + v4.11: No fallback to unprotected market order when price is missing."""
    strategy = _make_strategy_stub()
    strategy.latest_price_data = {}
    strategy.latest_signal_data = {}
    strategy.indicator_manager.recent_bars = []
    strategy.cache = DummyCache([])

    strategy._submit_bracket_order(OrderSide.SELL, 0.02)

    # v3.18: Should NOT submit any order (no unprotected market orders)
    strategy._submit_order.assert_not_called()
    assert strategy.order_factory.limit_kwargs is None, "No LIMIT entry when price missing"
    assert strategy._pending_sltp is None
    assert strategy._pending_entry_order_id is None


if __name__ == "__main__":
    test_submit_bracket_order_stores_pending_sltp()
    test_submit_bracket_order_blocks_when_price_missing()
    print("✅ bracket order tests passed")
