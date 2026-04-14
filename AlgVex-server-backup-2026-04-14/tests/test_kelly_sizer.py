"""Tests for Kelly position sizing (v2.0)."""
import pytest
from utils.kelly_sizer import KellySizer

@pytest.fixture
def kelly():
    config = {
        "kelly": {
            "enabled": True,
            "fraction": 0.25,
            "min_trades_for_kelly": 50,
            "kelly_blend_full_at": 100,
            "min_position_pct": 5,
            "max_position_pct": 100,
        }
    }
    return KellySizer(config=config)

class TestKellySizer:
    def test_warmup_mode(self, kelly):
        pct, details = kelly.calculate(confidence="HIGH", regime="RANGING")
        assert details["source"].startswith("fixed")
        assert 5 <= pct <= 100

    def test_regime_affects_size(self, kelly):
        pct_trend, _ = kelly.calculate(confidence="HIGH", regime="TRENDING_UP")
        pct_vol, _ = kelly.calculate(confidence="HIGH", regime="HIGH_VOLATILITY")
        assert pct_trend > pct_vol  # High vol should reduce size

    def test_confidence_ordering(self, kelly):
        hi, _ = kelly.calculate(confidence="HIGH", regime="RANGING")
        med, _ = kelly.calculate(confidence="MEDIUM", regime="RANGING")
        lo, _ = kelly.calculate(confidence="LOW", regime="RANGING")
        assert hi > med > lo

    def test_drawdown_scaling(self, kelly):
        normal, _ = kelly.calculate(confidence="MEDIUM", regime="RANGING", current_dd_pct=2, dd_threshold_pct=15)
        stressed, _ = kelly.calculate(confidence="MEDIUM", regime="RANGING", current_dd_pct=12, dd_threshold_pct=15)
        assert normal >= stressed

    def test_clamp_bounds(self, kelly):
        pct, _ = kelly.calculate(confidence="LOW", regime="HIGH_VOLATILITY", current_dd_pct=14, dd_threshold_pct=15)
        assert pct >= 5  # min
        assert pct <= 100  # max
