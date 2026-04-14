"""
Acceptance Tests for IMPLEMENTATION_PLAN.md

Section 3.3: Technical Acceptance Standards
Section 二 (EVALUATION_FRAMEWORK): Technical Implementation Standards

These tests verify that the implementation meets the documented requirements.

Run with: python3 -m pytest tests/test_implementation_plan.py -v
"""

import pytest
import time
import sys
import os
from pathlib import Path
from unittest.mock import Mock, MagicMock

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))


class TestDataCompleteness:
    """
    Test data completeness requirements.

    IMPLEMENTATION_PLAN Section 3.3:
    - Data completeness: ≥95%
    - Historical data should contain 20 values
    """

    def test_history_completeness(self):
        """
        历史数据应包含 20 个值

        EVALUATION_FRAMEWORK Section 2.1:
        数据完整性 ≥95% 无缺失
        """
        from indicators.technical_manager import TechnicalIndicatorManager

        # Create manager with test data
        manager = TechnicalIndicatorManager()

        # Create mock bars
        mock_bars = []
        for i in range(25):  # More than 20 to ensure we have enough
            bar = Mock()
            bar.close = 100 + i
            bar.high = 101 + i
            bar.low = 99 + i
            bar.open = 100 + i
            bar.volume = 1000 + i * 10
            bar.ts_init = i * 1000000000  # Nanoseconds
            mock_bars.append(bar)

        # Update manager with bars
        for bar in mock_bars:
            manager.update(bar)

        # Test get_historical_context
        context = manager.get_historical_context(count=20)

        assert len(context['price_trend']) == 20, f"Expected 20 prices, got {len(context['price_trend'])}"
        assert context['data_points'] == 20
        assert context['trend_direction'] != "INSUFFICIENT_DATA"

    def test_kline_data_completeness(self):
        """
        K线数据应返回请求的数量
        """
        from indicators.technical_manager import TechnicalIndicatorManager

        manager = TechnicalIndicatorManager()

        # Add enough bars
        for i in range(30):
            bar = Mock()
            bar.close = 100 + i
            bar.high = 101 + i
            bar.low = 99 + i
            bar.open = 100 + i
            bar.volume = 1000
            bar.ts_init = i * 1000000000
            manager.update(bar)

        kline_data = manager.get_kline_data(count=20)
        assert len(kline_data) == 20, f"Expected 20 klines, got {len(kline_data)}"


class TestPerformanceMetrics:
    """
    Test performance requirements.

    IMPLEMENTATION_PLAN Section 3.3:
    - Processing latency: <50ms (target), <100ms (max)
    - Memory increment: <30MB (target), <50MB (max)
    """

    def test_processing_latency(self):
        """
        数据处理延迟应 < 100ms

        EVALUATION_FRAMEWORK Section 2.2:
        数据处理延迟: < 50ms (目标), < 100ms (最大)
        """
        from indicators.technical_manager import TechnicalIndicatorManager

        manager = TechnicalIndicatorManager()

        # Add initial data
        for i in range(50):
            bar = Mock()
            bar.close = 100 + i * 0.1
            bar.high = 101 + i * 0.1
            bar.low = 99 + i * 0.1
            bar.open = 100 + i * 0.1
            bar.volume = 1000
            bar.ts_init = i * 1000000000
            manager.update(bar)

        # Measure processing time
        start = time.time()

        # Get all technical data (main processing)
        _ = manager.get_technical_data(current_price=105.0)
        _ = manager.get_historical_context(count=20)
        _ = manager.get_kline_data(count=10)

        elapsed_ms = (time.time() - start) * 1000

        assert elapsed_ms < 100, f"Processing too slow: {elapsed_ms:.1f}ms (max 100ms)"
        print(f"Processing latency: {elapsed_ms:.1f}ms (target <50ms, max <100ms)")

    def test_update_latency(self):
        """
        单次更新延迟应很小
        """
        from indicators.technical_manager import TechnicalIndicatorManager

        manager = TechnicalIndicatorManager()

        # Add initial data
        for i in range(50):
            bar = Mock()
            bar.close = 100 + i * 0.1
            bar.high = 101 + i * 0.1
            bar.low = 99 + i * 0.1
            bar.open = 100 + i * 0.1
            bar.volume = 1000
            bar.ts_init = i * 1000000000
            manager.update(bar)

        # Measure single update time
        bar = Mock()
        bar.close = 105.5
        bar.high = 106.0
        bar.low = 105.0
        bar.open = 105.2
        bar.volume = 1200
        bar.ts_init = 100 * 1000000000

        start = time.time()
        manager.update(bar)
        elapsed_ms = (time.time() - start) * 1000

        assert elapsed_ms < 10, f"Single update too slow: {elapsed_ms:.1f}ms"


class TestEmpyricalMetrics:
    """
    Test empyrical library integration.

    IMPLEMENTATION_PLAN Section 1.2.1:
    - performance_service.py should use empyrical for metrics
    - Annualization factor: 365 for crypto (NOT 252)
    """

    def test_empyrical_available(self):
        """
        empyrical 库应该可用
        """
        try:
            import empyrical as ep
            assert hasattr(ep, 'sharpe_ratio')
            assert hasattr(ep, 'sortino_ratio')
            assert hasattr(ep, 'calmar_ratio')
            assert hasattr(ep, 'max_drawdown')
            assert hasattr(ep, 'value_at_risk')
        except ImportError:
            pytest.skip("empyrical not installed")

    def test_sharpe_ratio_calculation(self):
        """
        Sharpe ratio 应使用正确的年化因子 (365)
        """
        try:
            import empyrical as ep
            import pandas as pd
            import numpy as np

            # Create sample returns
            np.random.seed(42)
            returns = pd.Series(np.random.normal(0.001, 0.02, 100))

            # Calculate with crypto annualization (365)
            sharpe_crypto = ep.sharpe_ratio(returns, annualization=365)

            # Calculate with stock annualization (252) - should be different
            sharpe_stock = ep.sharpe_ratio(returns, annualization=252)

            # They should be different due to different annualization
            assert sharpe_crypto != sharpe_stock, "Annualization factor should affect result"

            # Crypto sharpe should be higher due to higher annualization
            # (more trading days = higher annualized return)
            print(f"Sharpe (crypto, 365): {sharpe_crypto:.4f}")
            print(f"Sharpe (stock, 252): {sharpe_stock:.4f}")

        except ImportError:
            pytest.skip("empyrical not installed")

    def test_performance_service_metrics(self):
        """
        performance_service 应该返回所有 v3.0.1 指标
        """
        # Check that the service has the expected metrics structure
        expected_metrics = [
            'sharpe_ratio',
            'sortino_ratio',
            'calmar_ratio',
            'var_95',
            'cvar_99',
            'max_drawdown',
            'max_drawdown_percent',
        ]

        # Read the service file to verify metrics are defined
        service_path = project_root / 'web' / 'backend' / 'services' / 'performance_service.py'

        if service_path.exists():
            content = service_path.read_text()

            for metric in expected_metrics:
                assert metric in content, f"Missing metric in performance_service: {metric}"

            # Check for empyrical usage
            assert 'empyrical' in content, "performance_service should import empyrical"
            # Check for 365 annualization (crypto 24/7 trading)
            # Code may use either direct value or variable assignment
            assert 'annualization = 365' in content or 'annualization=365' in content, \
                "Should use 365 for crypto annualization"
        else:
            pytest.skip("performance_service.py not found")


class TestHistoricalContext:
    """
    Test historical context functionality.

    IMPLEMENTATION_PLAN Section 4.2.1:
    - get_historical_context should return trend data
    - Should include price_trend, volume_trend, rsi_trend, macd_trend
    """

    def test_historical_context_structure(self):
        """
        历史上下文应该包含所有必需字段
        """
        from indicators.technical_manager import TechnicalIndicatorManager

        manager = TechnicalIndicatorManager()

        # Add enough data
        for i in range(30):
            bar = Mock()
            bar.close = 100 + i
            bar.high = 101 + i
            bar.low = 99 + i
            bar.open = 100 + i
            bar.volume = 1000 + i * 10
            bar.ts_init = i * 1000000000
            manager.update(bar)

        context = manager.get_historical_context(count=20)

        # Check required fields from Section 4.2.1
        assert 'price_trend' in context
        assert 'volume_trend' in context
        assert 'rsi_trend' in context
        assert 'macd_trend' in context
        assert 'trend_direction' in context
        assert 'momentum_shift' in context

    def test_trend_direction_values(self):
        """
        趋势方向应该是有效值
        """
        from indicators.technical_manager import TechnicalIndicatorManager

        manager = TechnicalIndicatorManager()

        # Add data with clear uptrend
        for i in range(30):
            bar = Mock()
            bar.close = 100 + i * 2  # Clear uptrend
            bar.high = 101 + i * 2
            bar.low = 99 + i * 2
            bar.open = 100 + i * 2
            bar.volume = 1000
            bar.ts_init = i * 1000000000
            manager.update(bar)

        context = manager.get_historical_context(count=20)

        valid_directions = ['BULLISH', 'BEARISH', 'NEUTRAL', 'INSUFFICIENT_DATA']
        assert context['trend_direction'] in valid_directions

    def test_momentum_shift_values(self):
        """
        动量变化应该是有效值
        """
        from indicators.technical_manager import TechnicalIndicatorManager

        manager = TechnicalIndicatorManager()

        for i in range(30):
            bar = Mock()
            bar.close = 100 + i
            bar.high = 101 + i
            bar.low = 99 + i
            bar.open = 100 + i
            bar.volume = 1000
            bar.ts_init = i * 1000000000
            manager.update(bar)

        context = manager.get_historical_context(count=20)

        valid_momentum = ['INCREASING', 'DECREASING', 'STABLE', 'INSUFFICIENT_DATA']
        assert context['momentum_shift'] in valid_momentum


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
