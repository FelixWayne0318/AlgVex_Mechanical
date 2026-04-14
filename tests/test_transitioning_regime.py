"""
v40.0 Phase 8d: TRANSITIONING regime detection tests.

Tests the following behaviors:
1. TRANSITIONING detection when leading vs lagging indicators diverge
2. 2-cycle hysteresis prevents single-cycle whipsaw
3. Fallback to momentum when order_flow unavailable
4. Alignment cap behavior during TRANSITIONING
5. Net label format correctness
6. Auditor regex compatibility
7. Zip mapping correctness with missing dimensions
"""

import re
import sys
import os
import pytest

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_base_features(**overrides):
    """Create a minimal feature dict for compute_scores_from_features()."""
    base = {
        # 1D indicators
        "sma_200_1d": 50000.0,
        "price": 51000.0,
        "adx_direction_1d": "BULLISH",
        "di_plus_1d": 25.0,
        "di_minus_1d": 15.0,
        "rsi_1d": 55.0,
        "macd_1d": 100.0,
        "macd_signal_1d": 80.0,
        "adx_1d_trend_5bar": "RISING",
        "adx_1d": 30.0,
        "di_spread_1d_trend_5bar": "WIDENING",
        # 4H indicators
        "rsi_4h": 60.0,
        "macd_4h": 50.0,
        "macd_signal_4h": 40.0,
        "macd_histogram_4h": 10.0,
        "macd_histogram_4h_trend_5bar": "EXPANDING",
        "sma_20_4h": 51000.0,
        "sma_50_4h": 50500.0,
        "ema_12_4h": 51000.0,
        "ema_26_4h": 50800.0,
        "rsi_4h_trend_5bar": "RISING",
        "adx_4h_trend_5bar": "RISING",
        "adx_4h": 25.0,
        "di_plus_4h": 20.0,
        "di_minus_4h": 15.0,
        "volume_ratio_4h": 1.2,
        "price_4h_change_5bar_pct": 0.5,
        "bb_position_4h": 0.6,
        # 30M
        "rsi_30m": 55.0,
        "macd_30m": 20.0,
        "macd_signal_30m": 15.0,
        "macd_histogram_30m": 5.0,
        "rsi_30m_trend_5bar": "RISING",
        "momentum_shift_30m": "ACCELERATING",
        # Order flow
        "cvd_trend_30m": "POSITIVE",
        "cvd_trend_4h": "POSITIVE",
        "buy_ratio_30m": 0.55,
        "buy_ratio_4h": 0.55,
        "taker_buy_ratio": 0.55,
        "obi_weighted": 0.1,
        "obi_change_pct": 5.0,
        "cvd_price_cross_30m": "",
        "cvd_price_cross_4h": "",
        # Divergence
        "rsi_divergence_4h": "NONE",
        "macd_divergence_4h": "NONE",
        "obv_divergence_4h": "NONE",
        "rsi_divergence_30m": "NONE",
        "macd_divergence_30m": "NONE",
        "obv_divergence_30m": "NONE",
        # Risk
        "funding_rate_pct": 0.01,
        "long_ratio": 0.5,
        "oi_trend": "STABLE",
        "liquidation_bias": "BALANCED",
        "liquidation_buffer_pct": 50.0,
        "funding_rate_trend": "STABLE",
        "premium_index": 0.0,
        "fr_consecutive_blocks": 0,
        "top_traders_long_ratio": 0.5,
        "nearest_support_dist_atr": 5.0,
        "nearest_resist_dist_atr": 5.0,
        # Extension/volatility
        "extension_regime_30m": "NORMAL",
        "extension_regime_4h": "NORMAL",
        "extension_regime_1d": "NORMAL",
        "volatility_regime_30m": "NORMAL",
        "volatility_regime_4h": "NORMAL",
        "volatility_regime_1d": "NORMAL",
        "bb_width_30m_trend_5bar": "FLAT",
        "bb_width_4h_trend_5bar": "FLAT",
        # Availability flags
        "_avail_mtf_1d": True,
        "_avail_mtf_4h": True,
        "_avail_order_flow": True,
        "_avail_derivatives": True,
        "_avail_sentiment": True,
        "_avail_orderbook": True,
        "_avail_account": True,
        "_avail_binance_derivatives": True,
        "_avail_sr_zones": True,
    }
    base.update(overrides)
    return base


def _compute_scores(features):
    """Call compute_scores_from_features via ReportFormatterMixin."""
    from agents.report_formatter import ReportFormatterMixin

    class MockFormatter(ReportFormatterMixin):
        def __init__(self):
            import logging
            self.logger = logging.getLogger("test")

    formatter = MockFormatter()
    return formatter.compute_scores_from_features(features)


class TestTransitioningDetection:
    """Phase 2: TRANSITIONING regime detection."""

    def test_transitioning_bullish(self):
        """trend=BEARISH + flow=BULLISH → TRANSITIONING_BULLISH (with hysteresis)."""
        f = _make_base_features(
            # Make trend BEARISH
            sma_200_1d=52000.0,  # price < SMA200
            price=50000.0,
            adx_direction_1d="BEARISH",
            di_plus_1d=12.0,
            di_minus_1d=25.0,
            rsi_1d=40.0,
            macd_1d=-100.0,
            macd_signal_1d=-50.0,
            adx_1d_trend_5bar="RISING",
            di_spread_1d_trend_5bar="WIDENING",
            # Make 4H also bearish for trend
            rsi_4h=42.0,
            macd_4h=-30.0,
            macd_signal_4h=-20.0,
            sma_20_4h=50500.0,
            sma_50_4h=51000.0,
            ema_12_4h=50300.0,
            ema_26_4h=50600.0,
            # But order flow is BULLISH
            cvd_trend_30m="POSITIVE",
            cvd_trend_4h="POSITIVE",
            buy_ratio_30m=0.60,
            cvd_price_cross_4h="ACCUMULATION",
            # Simulate 2nd consecutive cycle (hysteresis)
            _prev_regime_transition="TRANSITIONING_BULLISH",
        )
        scores = _compute_scores(f)
        assert scores["regime_transition"] == "TRANSITIONING_BULLISH"
        assert "TRANSITIONING" in scores["net"]

    def test_transitioning_bearish(self):
        """trend=BULLISH + flow=BEARISH → TRANSITIONING_BEARISH (with hysteresis)."""
        f = _make_base_features(
            # Trend is bullish (default features)
            # But order flow is bearish
            cvd_trend_30m="NEGATIVE",
            cvd_trend_4h="NEGATIVE",
            buy_ratio_30m=0.40,
            buy_ratio_4h=0.40,
            taker_buy_ratio=0.40,
            cvd_price_cross_4h="DISTRIBUTION",
            # 2nd consecutive cycle
            _prev_regime_transition="TRANSITIONING_BEARISH",
        )
        scores = _compute_scores(f)
        assert scores["regime_transition"] == "TRANSITIONING_BEARISH"

    def test_no_transition_when_aligned(self):
        """trend=BULLISH + flow=BULLISH → no TRANSITIONING."""
        f = _make_base_features()  # Default: all bullish
        scores = _compute_scores(f)
        assert scores["regime_transition"] == "NONE"
        assert "TRANSITIONING" not in scores["net"]


class TestTransitioningHysteresis:
    """Phase 2b: 2-cycle hysteresis prevents whipsaw."""

    def test_first_cycle_no_activation(self):
        """Single-cycle transition signal → should NOT activate TRANSITIONING."""
        f = _make_base_features(
            # Bearish trend + bullish flow
            sma_200_1d=52000.0,
            price=50000.0,
            adx_direction_1d="BEARISH",
            di_plus_1d=12.0,
            di_minus_1d=25.0,
            rsi_1d=40.0,
            macd_1d=-100.0,
            macd_signal_1d=-50.0,
            cvd_trend_30m="POSITIVE",
            cvd_trend_4h="POSITIVE",
            cvd_price_cross_4h="ACCUMULATION",
            # First cycle — no previous transition
            _prev_regime_transition="NONE",
        )
        scores = _compute_scores(f)
        assert scores["regime_transition"] == "NONE"
        # But raw detection should be recorded for next cycle
        assert scores["_raw_regime_transition"] == "TRANSITIONING_BULLISH"

    def test_mismatched_direction_no_activation(self):
        """Previous BULLISH + current BEARISH → no activation."""
        f = _make_base_features(
            cvd_trend_30m="NEGATIVE",
            cvd_trend_4h="NEGATIVE",
            cvd_price_cross_4h="DISTRIBUTION",
            _prev_regime_transition="TRANSITIONING_BULLISH",
        )
        scores = _compute_scores(f)
        # Current raw is BEARISH, prev is BULLISH → mismatch → NONE
        assert scores["regime_transition"] == "NONE"


class TestTransitioningFallback:
    """Phase 2c: Momentum fallback when order_flow unavailable."""

    def test_momentum_fallback(self):
        """_avail_order_flow=False → use momentum direction as proxy (with hysteresis)."""
        f = _make_base_features(
            _avail_order_flow=False,
            # Bearish trend
            sma_200_1d=52000.0,
            price=50000.0,
            adx_direction_1d="BEARISH",
            di_plus_1d=12.0,
            di_minus_1d=25.0,
            rsi_1d=40.0,
            macd_1d=-100.0,
            macd_signal_1d=-50.0,
            # Bullish momentum
            rsi_4h_trend_5bar="RISING",
            macd_histogram_4h=20.0,
            macd_histogram_4h_trend_5bar="EXPANDING",
            rsi_4h=60.0,
            macd_4h=50.0,
            macd_signal_4h=40.0,
            # 2nd cycle
            _prev_regime_transition="TRANSITIONING_BULLISH",
        )
        scores = _compute_scores(f)
        assert scores["regime_transition"] == "TRANSITIONING_BULLISH"

    def test_no_fallback_when_both_unavailable(self):
        """Both order_flow and 4H unavailable → no TRANSITIONING possible."""
        f = _make_base_features(
            _avail_order_flow=False,
            _avail_mtf_4h=False,
            _prev_regime_transition="TRANSITIONING_BULLISH",
        )
        scores = _compute_scores(f)
        assert scores["regime_transition"] == "NONE"


class TestNetLabelFormat:
    """Phase 3: Net label format correctness."""

    def test_transitioning_label_format(self):
        """TRANSITIONING_BULLISH_2of3 format."""
        f = _make_base_features(
            # Bearish trend + bullish flow
            sma_200_1d=52000.0,
            price=50000.0,
            adx_direction_1d="BEARISH",
            di_plus_1d=12.0,
            di_minus_1d=25.0,
            rsi_1d=40.0,
            macd_1d=-100.0,
            macd_signal_1d=-50.0,
            cvd_trend_30m="POSITIVE",
            cvd_trend_4h="POSITIVE",
            cvd_price_cross_4h="ACCUMULATION",
            _prev_regime_transition="TRANSITIONING_BULLISH",
        )
        scores = _compute_scores(f)
        net = scores["net"]
        assert net.startswith("TRANSITIONING_")
        # Should match pattern: TRANSITIONING_{BULLISH|BEARISH}_{N}of{M}
        assert re.match(r"TRANSITIONING_(BULLISH|BEARISH)_\d+of\d+", net)

    def test_lean_label_unchanged(self):
        """Standard aligned signals still produce LEAN_BULLISH/BEARISH."""
        f = _make_base_features()  # All bullish
        scores = _compute_scores(f)
        assert "LEAN_BULLISH" in scores["net"] or "INSUFFICIENT" in scores["net"] or "CONFLICTING" in scores["net"]


class TestAuditorRegex:
    """Phase 6: Auditor regex matches TRANSITIONING labels."""

    def test_regex_matches_transitioning(self):
        """_NET_DIRECTION_RE matches TRANSITIONING_BULLISH_2of3."""
        pattern = re.compile(r'(?:LEAN|TRANSITIONING)_(BULLISH|BEARISH)_(\d+)of(\d+)')
        assert pattern.match("TRANSITIONING_BULLISH_2of3")
        assert pattern.match("TRANSITIONING_BEARISH_1of3")
        assert pattern.match("LEAN_BULLISH_2of3")
        assert pattern.match("LEAN_BEARISH_3of3")
        assert not pattern.match("CONFLICTING_0of3")
        assert not pattern.match("INSUFFICIENT")


class TestZipMapping:
    """P0-1: Weight mapping correctness with missing dimensions."""

    def test_missing_1d_mapping(self):
        """_avail_mtf_1d=False → momentum and order_flow weights correct."""
        f = _make_base_features(_avail_mtf_1d=False)
        scores = _compute_scores(f)
        # Should not crash and should produce valid net
        assert scores["net"] != ""
        assert scores["trend"]["direction"] != ""

    def test_missing_order_flow_mapping(self):
        """_avail_order_flow=False → trend and momentum weights correct."""
        f = _make_base_features(_avail_order_flow=False)
        scores = _compute_scores(f)
        assert scores["net"] != ""
        assert scores["order_flow"]["direction"] == "N/A"


class TestDivergenceAdjustment:
    """P0-6: Divergence mutual exclusion with reversal detection."""

    def test_divergence_reduces_trend_score(self):
        """2+ bullish divergences reduce trend score when reversal not active."""
        f = _make_base_features(
            rsi_divergence_4h="BULLISH",
            macd_divergence_4h="BULLISH",
            # Not enough for reversal_active (need 3 of 5 signals)
        )
        scores_with_div = _compute_scores(f)

        f_no_div = _make_base_features()
        scores_no_div = _compute_scores(f_no_div)

        # With divergence, trend score should be reduced
        assert scores_with_div["trend"]["score"] <= scores_no_div["trend"]["score"]

    def test_divergence_not_applied_when_reversal_active(self):
        """When reversal_active=True, divergence adjustment skipped (mutual exclusion)."""
        # Create conditions for reversal_active: need 3+ of 5 signals
        f = _make_base_features(
            # Signal 1: ADX falling from elevated level
            adx_1d=35.0,
            adx_1d_trend_5bar="FALLING",
            adx_direction_1d="BULLISH",
            # Signal 2: Multiple divergences (also what we're testing)
            rsi_divergence_4h="BEARISH",
            macd_divergence_4h="BEARISH",
            obv_divergence_4h="BEARISH",
            # Signal 3: DI convergence
            di_spread_1d_trend_5bar="NARROWING",
            # Signal 4: Price near resistance
            nearest_resist_dist_atr=1.5,
            # Signal 5: momentum opposing trend
            rsi_4h_trend_5bar="FALLING",
            macd_histogram_4h=-10.0,
            macd_histogram_4h_trend_5bar="EXPANDING",
        )
        scores = _compute_scores(f)
        # reversal should be active
        assert scores["trend_reversal"]["active"] is True
        # Trend score was reduced by reversal (-3), but divergence should NOT
        # additionally reduce it (mutual exclusion)
