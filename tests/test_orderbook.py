# tests/test_orderbook.py

import pytest
from utils.binance_orderbook_client import BinanceOrderBookClient
from utils.orderbook_processor import OrderBookProcessor


class TestBinanceOrderBookClient:
    """测试 Binance 订单簿客户端"""

    def test_initialization(self):
        """测试初始化"""
        client = BinanceOrderBookClient(
            timeout=10,
            max_retries=2,
            retry_delay=1.0,
        )
        assert client.timeout == 10
        assert client.max_retries == 2
        assert client.retry_delay == 1.0

    def test_validate_orderbook_valid(self):
        """测试有效订单簿验证"""
        client = BinanceOrderBookClient()

        valid_orderbook = {
            "bids": [
                ["100.0", "10.0"],
                ["99.5", "5.0"],
                ["99.0", "8.0"],
            ],
            "asks": [
                ["100.5", "8.0"],
                ["101.0", "6.0"],
                ["101.5", "12.0"],
            ],
        }

        assert client._validate_orderbook(valid_orderbook) is True

    def test_validate_orderbook_empty(self):
        """测试空订单簿"""
        client = BinanceOrderBookClient()

        empty_orderbook = {
            "bids": [],
            "asks": [],
        }

        assert client._validate_orderbook(empty_orderbook) is False

    def test_validate_orderbook_crossed(self):
        """测试交叉盘 (best_bid >= best_ask)"""
        client = BinanceOrderBookClient()

        crossed_orderbook = {
            "bids": [["100.0", "10.0"]],
            "asks": [["99.0", "8.0"]],  # 价格错误
        }

        assert client._validate_orderbook(crossed_orderbook) is False

    def test_validate_orderbook_non_monotonic_bids(self):
        """测试买单价格非单调递减"""
        client = BinanceOrderBookClient()

        invalid_orderbook = {
            "bids": [
                ["99.0", "10.0"],
                ["100.0", "5.0"],  # 价格递增，错误
            ],
            "asks": [["100.5", "8.0"]],
        }

        assert client._validate_orderbook(invalid_orderbook) is False


class TestOrderBookProcessor:
    """测试订单簿处理器"""

    @pytest.fixture
    def sample_orderbook(self):
        """示例订单簿数据"""
        return {
            "bids": [
                ["100.0", "10.0"],
                ["99.5", "5.0"],
                ["99.0", "8.0"],
                ["98.5", "12.0"],
                ["98.0", "6.0"],
            ],
            "asks": [
                ["100.5", "8.0"],
                ["101.0", "6.0"],
                ["101.5", "12.0"],
                ["102.0", "10.0"],
                ["102.5", "5.0"],
            ],
        }

    def test_initialization(self):
        """测试处理器初始化"""
        processor = OrderBookProcessor(
            price_band_pct=0.5,
            base_anomaly_threshold=3.0,
            slippage_amounts=[0.1, 0.5, 1.0],
        )
        assert processor.price_band_pct == 0.5
        assert processor.base_anomaly_threshold == 3.0
        assert processor.slippage_amounts == [0.1, 0.5, 1.0]

    def test_simple_obi_calculation(self, sample_orderbook):
        """测试简单 OBI 计算"""
        processor = OrderBookProcessor()
        bids = sample_orderbook["bids"]
        asks = sample_orderbook["asks"]

        obi = processor._calculate_simple_obi(bids, asks)

        # OBI = (bid_vol - ask_vol) / (bid_vol + ask_vol)
        bid_vol = 10 + 5 + 8 + 12 + 6  # 41
        ask_vol = 8 + 6 + 12 + 10 + 5  # 41
        expected_obi = (bid_vol - ask_vol) / (bid_vol + ask_vol)

        assert obi == expected_obi

    def test_weighted_obi_calculation(self, sample_orderbook):
        """测试加权 OBI 计算"""
        processor = OrderBookProcessor()
        bids = sample_orderbook["bids"]
        asks = sample_orderbook["asks"]
        decay = 0.8

        weighted_obi = processor._calculate_weighted_obi(bids, asks, decay)

        # 加权计算: weighted_vol = sum(vol * decay^i)
        weighted_bid = 10 * 1.0 + 5 * 0.8 + 8 * 0.64 + 12 * 0.512 + 6 * 0.4096
        weighted_ask = 8 * 1.0 + 6 * 0.8 + 12 * 0.64 + 10 * 0.512 + 5 * 0.4096
        total = weighted_bid + weighted_ask
        expected = (weighted_bid - weighted_ask) / total

        assert abs(weighted_obi - expected) < 0.0001

    def test_adaptive_decay_high_volatility(self):
        """测试高波动时衰减因子降低"""
        processor = OrderBookProcessor()
        high_volatility = 0.05  # 5%

        decay = processor._calculate_adaptive_decay(high_volatility)

        # 高波动时，decay 应该低于 base_decay
        assert decay < processor.weighted_obi_config["base_decay"]

    def test_adaptive_decay_low_volatility(self):
        """测试低波动时衰减因子提高"""
        processor = OrderBookProcessor()
        low_volatility = 0.005  # 0.5%

        decay = processor._calculate_adaptive_decay(low_volatility)

        # 低波动时，decay 应该高于 base_decay
        assert decay > processor.weighted_obi_config["base_decay"]

    def test_dynamics_insufficient_data(self):
        """测试历史数据不足时返回 INSUFFICIENT_DATA"""
        processor = OrderBookProcessor()
        processor._history = []

        current_data = {
            "obi": {"simple": 0.15},
            "depth_distribution": {"bid_depth_usd": 1000, "ask_depth_usd": 900},
            "liquidity": {"spread_pct": 0.02},
        }

        dynamics = processor._calculate_dynamics(current_data)

        assert dynamics["trend"] == "INSUFFICIENT_DATA"
        assert dynamics["samples_count"] == 0

    def test_pressure_gradient_high_concentration(self, sample_orderbook):
        """测试高集中度识别"""
        processor = OrderBookProcessor()

        # 创建高集中度订单簿 (前5档占比很高)
        concentrated_bids = [
            ["100.0", "50.0"],  # 大部分在前几档
            ["99.5", "40.0"],
            ["99.0", "5.0"],
            ["98.5", "3.0"],
            ["98.0", "2.0"],
        ]
        concentrated_asks = sample_orderbook["asks"]

        gradient = processor._calculate_pressure_gradient(concentrated_bids, concentrated_asks)

        assert gradient["bid_concentration"] == "HIGH"
        assert gradient["bid_near_5"] > 0.4

    def test_no_data_result(self):
        """测试 NO_DATA 状态返回"""
        processor = OrderBookProcessor()

        result = processor._no_data_result("Test error")

        assert result["_status"]["code"] == "NO_DATA"
        assert "Test error" in result["_status"]["message"]
        assert result["obi"] is None
        assert result["dynamics"] is None

    def test_slippage_estimation(self, sample_orderbook):
        """测试滑点估算"""
        processor = OrderBookProcessor()
        asks = sample_orderbook["asks"]

        slippage = processor._estimate_slippage_with_confidence(
            orders=asks,
            amount=10.0,  # 买 10 BTC
            side="buy",
        )

        # 应该有估算值
        assert slippage["estimated"] is not None
        assert slippage["confidence"] > 0
        assert len(slippage["range"]) == 2

    def test_slippage_insufficient_depth(self):
        """测试深度不足时的滑点估算"""
        processor = OrderBookProcessor()

        # 深度不足的订单簿
        shallow_asks = [["100.0", "0.5"]]  # 只有 0.5 BTC

        slippage = processor._estimate_slippage_with_confidence(
            orders=shallow_asks,
            amount=10.0,  # 需要 10 BTC，深度不足
            side="buy",
        )

        assert slippage["estimated"] is None
        assert slippage["confidence"] == 0.0
        assert slippage["reason"] == "insufficient_depth"

    def test_full_process(self, sample_orderbook):
        """测试完整处理流程"""
        processor = OrderBookProcessor()

        result = processor.process(
            order_book=sample_orderbook,
            current_price=100.0,
            volatility=0.02,
        )

        # 验证返回结构
        assert "_status" in result
        assert result["_status"]["code"] == "OK"
        assert "obi" in result
        assert "dynamics" in result
        assert "pressure_gradient" in result
        assert "liquidity" in result
        assert "anomalies" in result

        # 验证 OBI 数据
        obi = result["obi"]
        assert "simple" in obi
        assert "weighted" in obi
        assert "adaptive_weighted" in obi
        assert "decay_used" in obi


class TestOrderBookIntegration:
    """集成测试"""

    def test_client_and_processor_integration(self):
        """测试客户端和处理器集成"""
        # 注意: 这个测试需要网络访问，可以标记为 @pytest.mark.integration
        # 或使用 mock 模拟 API 响应
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
