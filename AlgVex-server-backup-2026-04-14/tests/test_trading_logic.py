"""
Tests for trading_logic.py — the core R/R validation and trade evaluation engine.

Covers:
- validate_multiagent_sltp(): R/R gate (1.5:1 hard minimum, 1.95:1 counter-trend)
- evaluate_trade(): Trade grading (A+/A/B/C/D/F)
- calculate_mechanical_sltp(): ATR-based SL/TP calculation
- Edge cases: zero prices, inverted SL/TP, counter-trend detection
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(autouse=True)
def reset_trading_logic_config():
    """Reset the module-level config cache before each test."""
    import strategy.trading_logic as tl
    tl._TRADING_LOGIC_CONFIG = None
    yield
    tl._TRADING_LOGIC_CONFIG = None


@pytest.fixture
def mock_config():
    """Provide a mock config that returns sensible defaults."""
    config = {
        'min_rr_ratio': 1.5,
        'counter_trend_rr_multiplier': 1.3,
        'min_sl_distance_atr': 2.0,
        'min_sl_distance_pct': 0.003,
        'min_tp_distance_pct': 0.005,
        'default_sl_pct': 0.02,
        'default_tp_pct': 0.03,
        'mechanical_sltp': {
            'enabled': True,
            'sl_atr_multiplier': {'HIGH': 2.0, 'MEDIUM': 2.5},
            'tp_rr_target': {'HIGH': 2.5, 'MEDIUM': 2.0},
            'sl_atr_multiplier_floor': 1.5,
            'counter_trend_sl_tighten': 1.0,
        },
        'emergency_sl': {
            'base_pct': 0.02,
            'atr_multiplier': 1.5,
            'cooldown_seconds': 120,
            'max_consecutive': 3,
        },
    }
    with patch('strategy.trading_logic._get_trading_logic_config', return_value=config):
        yield config


# ============================================================================
# validate_multiagent_sltp tests
# ============================================================================

class TestValidateMultiagentSltp:
    """Test the R/R validation gate.

    With ATR=1200 and min_sl_distance_atr=2.0, min SL distance = 2400 (2.53%).
    So LONG SL must be <= entry - 2400 = 92600 for $95000 entry.
    """

    def test_valid_long_trade(self, mock_config):
        """Valid LONG: SL below entry, TP above, R/R >= 1.5."""
        from strategy.trading_logic import validate_multiagent_sltp
        valid, sl, tp, reason = validate_multiagent_sltp(
            side='LONG',
            multi_sl=92000.0,   # $3000 risk (> 2400 min)
            multi_tp=99500.0,   # $4500 reward → R/R 1.5
            entry_price=95000.0,
            atr_value=1200.0,
        )
        assert valid is True
        assert sl == 92000.0
        assert tp == 99500.0

    def test_valid_short_trade(self, mock_config):
        """Valid SHORT: SL above entry, TP below, R/R >= 1.5."""
        from strategy.trading_logic import validate_multiagent_sltp
        valid, sl, tp, reason = validate_multiagent_sltp(
            side='SHORT',
            multi_sl=98000.0,   # $3000 risk
            multi_tp=90500.0,   # $4500 reward → R/R 1.5
            entry_price=95000.0,
            atr_value=1200.0,
        )
        assert valid is True

    def test_reject_low_rr_long(self, mock_config):
        """LONG with R/R < 1.5 should be rejected."""
        from strategy.trading_logic import validate_multiagent_sltp
        valid, sl, tp, reason = validate_multiagent_sltp(
            side='LONG',
            multi_sl=92000.0,   # $3000 risk
            multi_tp=95500.0,   # $500 reward → R/R 0.17
            entry_price=95000.0,
            atr_value=1200.0,
        )
        assert valid is False
        assert 'R/R' in reason

    def test_reject_low_rr_short(self, mock_config):
        """SHORT with R/R < 1.5 should be rejected."""
        from strategy.trading_logic import validate_multiagent_sltp
        valid, sl, tp, reason = validate_multiagent_sltp(
            side='SHORT',
            multi_sl=98000.0,   # $3000 risk
            multi_tp=94500.0,   # $500 reward → R/R 0.17
            entry_price=95000.0,
            atr_value=1200.0,
        )
        assert valid is False

    def test_reject_inverted_sl_long(self, mock_config):
        """LONG with SL above entry should fail."""
        from strategy.trading_logic import validate_multiagent_sltp
        valid, _, _, reason = validate_multiagent_sltp(
            side='LONG',
            multi_sl=96000.0,   # SL above entry = wrong direction
            multi_tp=100000.0,
            entry_price=95000.0,
            atr_value=1200.0,
        )
        assert valid is False

    def test_reject_inverted_sl_short(self, mock_config):
        """SHORT with SL below entry should fail."""
        from strategy.trading_logic import validate_multiagent_sltp
        valid, _, _, reason = validate_multiagent_sltp(
            side='SHORT',
            multi_sl=94000.0,   # SL below entry = wrong direction
            multi_tp=92000.0,
            entry_price=95000.0,
            atr_value=1200.0,
        )
        assert valid is False

    def test_reject_inverted_tp_long(self, mock_config):
        """LONG with TP below entry should fail."""
        from strategy.trading_logic import validate_multiagent_sltp
        valid, _, _, reason = validate_multiagent_sltp(
            side='LONG',
            multi_sl=92000.0,
            multi_tp=94000.0,   # TP below entry = wrong direction
            entry_price=95000.0,
            atr_value=1200.0,
        )
        assert valid is False

    def test_reject_none_sl(self, mock_config):
        """Missing SL should fail."""
        from strategy.trading_logic import validate_multiagent_sltp
        valid, _, _, reason = validate_multiagent_sltp(
            side='LONG',
            multi_sl=None,
            multi_tp=98000.0,
            entry_price=95000.0,
        )
        assert valid is False
        assert 'not provided' in reason

    def test_counter_trend_rr_escalation(self, mock_config):
        """Counter-trend trades should require R/R >= 1.95 (1.5 × 1.3)."""
        from strategy.trading_logic import validate_multiagent_sltp

        # R/R = 1.7 — passes normal (>= 1.5) but fails counter-trend (>= 1.95)
        trend_info = {
            'trend': 'BEARISH',
            'sma_20': 96000.0,
            'sma_50': 97000.0,
        }
        valid, _, _, reason = validate_multiagent_sltp(
            side='LONG',
            multi_sl=92000.0,   # $3000 risk
            multi_tp=100100.0,  # $5100 reward → R/R 1.7
            entry_price=95000.0,
            atr_value=1200.0,
            trend_info=trend_info,
        )
        assert valid is False
        assert 'counter-trend' in reason

    def test_counter_trend_high_rr_passes(self, mock_config):
        """Counter-trend with R/R >= 1.95 should pass."""
        from strategy.trading_logic import validate_multiagent_sltp

        trend_info = {
            'trend': 'BEARISH',
            'sma_20': 96000.0,
            'sma_50': 97000.0,
        }
        valid, sl, tp, reason = validate_multiagent_sltp(
            side='LONG',
            multi_sl=92000.0,   # $3000 risk
            multi_tp=101000.0,  # $6000 reward → R/R 2.0
            entry_price=95000.0,
            atr_value=1200.0,
            trend_info=trend_info,
        )
        assert valid is True

    def test_exact_rr_threshold(self, mock_config):
        """R/R exactly at 1.5 should pass."""
        from strategy.trading_logic import validate_multiagent_sltp
        valid, _, _, reason = validate_multiagent_sltp(
            side='LONG',
            multi_sl=92000.0,   # $3000 risk
            multi_tp=99500.0,   # $4500 reward → R/R 1.5
            entry_price=95000.0,
            atr_value=1200.0,
        )
        assert valid is True

    def test_sl_too_close_rejected(self, mock_config):
        """SL distance below ATR minimum should be rejected."""
        from strategy.trading_logic import validate_multiagent_sltp
        valid, _, _, reason = validate_multiagent_sltp(
            side='LONG',
            multi_sl=94000.0,   # Only $1000 risk (< 2400 min)
            multi_tp=99000.0,
            entry_price=95000.0,
            atr_value=1200.0,
        )
        assert valid is False
        assert 'too close' in reason


# ============================================================================
# evaluate_trade tests
# ============================================================================

class TestEvaluateTrade:
    """Test trade grading system."""

    def test_grade_a_plus(self, mock_config):
        """R/R >= 2.5 profit should get A+."""
        from strategy.trading_logic import evaluate_trade
        result = evaluate_trade(
            entry_price=95000.0,
            exit_price=100000.0,  # $5000 profit
            planned_sl=93000.0,   # $2000 risk → R/R 2.5
            planned_tp=100000.0,
            direction='LONG',
            pnl_pct=5.26,
        )
        assert result['grade'] == 'A+'
        assert result['actual_rr'] >= 2.5

    def test_grade_a(self, mock_config):
        """R/R >= 1.5 profit should get A."""
        from strategy.trading_logic import evaluate_trade
        result = evaluate_trade(
            entry_price=95000.0,
            exit_price=98000.0,  # $3000 profit
            planned_sl=93000.0,  # $2000 risk → R/R 1.5
            planned_tp=100000.0,
            direction='LONG',
            pnl_pct=3.16,
        )
        assert result['grade'] == 'A'

    def test_grade_b(self, mock_config):
        """R/R >= 1.0 profit should get B."""
        from strategy.trading_logic import evaluate_trade
        result = evaluate_trade(
            entry_price=95000.0,
            exit_price=97000.0,  # $2000 profit
            planned_sl=93000.0,  # $2000 risk → R/R 1.0
            planned_tp=100000.0,
            direction='LONG',
            pnl_pct=2.1,
        )
        assert result['grade'] == 'B'

    def test_grade_c(self, mock_config):
        """Small profit (R/R < 1.0) should get C."""
        from strategy.trading_logic import evaluate_trade
        result = evaluate_trade(
            entry_price=95000.0,
            exit_price=95500.0,  # $500 profit
            planned_sl=93000.0,  # $2000 risk → R/R 0.25
            planned_tp=100000.0,
            direction='LONG',
            pnl_pct=0.53,
        )
        assert result['grade'] == 'C'

    def test_grade_d_controlled_loss(self, mock_config):
        """Loss within SL × 1.2 should get D (disciplined)."""
        from strategy.trading_logic import evaluate_trade
        result = evaluate_trade(
            entry_price=95000.0,
            exit_price=93100.0,  # Hit SL area
            planned_sl=93000.0,
            planned_tp=100000.0,
            direction='LONG',
            pnl_pct=-2.0,
        )
        assert result['grade'] == 'D'

    def test_grade_f_uncontrolled_loss(self, mock_config):
        """Loss exceeding SL × 1.2 should get F (uncontrolled)."""
        from strategy.trading_logic import evaluate_trade
        result = evaluate_trade(
            entry_price=95000.0,
            exit_price=90000.0,  # Far beyond SL
            planned_sl=93000.0,
            planned_tp=100000.0,
            direction='LONG',
            pnl_pct=-5.26,
        )
        assert result['grade'] == 'F'

    def test_short_trade_evaluation(self, mock_config):
        """SHORT trade should evaluate correctly."""
        from strategy.trading_logic import evaluate_trade
        result = evaluate_trade(
            entry_price=95000.0,
            exit_price=91000.0,  # $4000 profit on short
            planned_sl=97000.0,  # $2000 risk → R/R 2.0
            planned_tp=90000.0,
            direction='SHORT',
            pnl_pct=4.21,
        )
        assert result['grade'] in ('A', 'A+')
        assert result['direction_correct'] is True

    def test_invalid_entry_price(self, mock_config):
        """Zero/negative entry should not crash."""
        from strategy.trading_logic import evaluate_trade
        result = evaluate_trade(
            entry_price=0.0,
            exit_price=95000.0,
            planned_sl=93000.0,
            planned_tp=100000.0,
            direction='LONG',
            pnl_pct=0.0,
        )
        assert 'grade' in result

    def test_evaluation_includes_hold_duration(self, mock_config):
        """Evaluation should calculate hold duration from timestamps."""
        from strategy.trading_logic import evaluate_trade
        result = evaluate_trade(
            entry_price=95000.0,
            exit_price=98000.0,
            planned_sl=93000.0,
            planned_tp=100000.0,
            direction='LONG',
            pnl_pct=3.16,
            entry_timestamp='2026-02-20T12:00:00Z',
            exit_timestamp='2026-02-20T16:30:00Z',
        )
        assert result['hold_duration_min'] == 270  # 4.5 hours

    def test_evaluation_with_atr_data(self, mock_config):
        """v11.5 ATR data should be included when provided."""
        from strategy.trading_logic import evaluate_trade
        result = evaluate_trade(
            entry_price=95000.0,
            exit_price=98000.0,
            planned_sl=93000.0,
            planned_tp=100000.0,
            direction='LONG',
            pnl_pct=3.16,
            atr_value=1200.0,
            sl_atr_multiplier=2.0,
            is_counter_trend=True,
        )
        assert result.get('atr_value') == 1200.0
        assert result.get('sl_atr_multiplier') == 2.0
        assert result.get('is_counter_trend') is True


# ============================================================================
# calculate_mechanical_sltp tests
# ============================================================================

class TestCalculateMechanicalSltp:
    """Test ATR-based mechanical SL/TP calculation."""

    def test_long_sltp_direction(self, mock_config):
        """LONG: SL should be below entry, TP above."""
        from strategy.trading_logic import calculate_mechanical_sltp
        success, sl, tp, desc = calculate_mechanical_sltp(
            entry_price=95000.0,
            atr_value=1200.0,
            side='LONG',
            confidence='HIGH',
        )
        assert success is True
        assert sl < 95000.0
        assert tp > 95000.0

    def test_short_sltp_direction(self, mock_config):
        """SHORT: SL should be above entry, TP below."""
        from strategy.trading_logic import calculate_mechanical_sltp
        success, sl, tp, desc = calculate_mechanical_sltp(
            entry_price=95000.0,
            atr_value=1200.0,
            side='SHORT',
            confidence='HIGH',
        )
        assert success is True
        assert sl > 95000.0
        assert tp < 95000.0

    def test_high_confidence_tighter_sl(self, mock_config):
        """HIGH confidence should have tighter SL than MEDIUM."""
        from strategy.trading_logic import calculate_mechanical_sltp
        _, sl_high, _, _ = calculate_mechanical_sltp(
            entry_price=95000.0, atr_value=1200.0, side='LONG', confidence='HIGH',
        )
        _, sl_medium, _, _ = calculate_mechanical_sltp(
            entry_price=95000.0, atr_value=1200.0, side='LONG', confidence='MEDIUM',
        )
        # HIGH uses 2.0 ATR mult, MEDIUM uses 2.5 → HIGH SL is closer to entry
        assert sl_high > sl_medium

    def test_sl_atr_floor_applied(self, mock_config):
        """SL should never be tighter than sl_atr_multiplier_floor × ATR."""
        from strategy.trading_logic import calculate_mechanical_sltp

        # Override HIGH multiplier to be below floor
        mock_config['mechanical_sltp']['sl_atr_multiplier'] = {'HIGH': 1.0, 'MEDIUM': 2.5}
        success, sl, tp, desc = calculate_mechanical_sltp(
            entry_price=95000.0,
            atr_value=1200.0,
            side='LONG',
            confidence='HIGH',
        )
        assert success is True
        # SL distance should be at least 1.5 × ATR = 1800
        sl_distance = 95000.0 - sl
        assert sl_distance >= 1200.0 * 1.5 * 0.99  # 0.99 for float tolerance

    def test_invalid_inputs(self, mock_config):
        """Zero ATR or price should return failure."""
        from strategy.trading_logic import calculate_mechanical_sltp
        success, _, _, _ = calculate_mechanical_sltp(
            entry_price=0.0, atr_value=1200.0, side='LONG', confidence='HIGH',
        )
        assert success is False

        success, _, _, _ = calculate_mechanical_sltp(
            entry_price=95000.0, atr_value=0.0, side='LONG', confidence='HIGH',
        )
        assert success is False

    def test_counter_trend_rr_raised_medium(self, mock_config):
        """Counter-trend with MEDIUM confidence should raise R/R above base.

        MEDIUM base R/R = 2.0, counter-trend min = 1.5×1.3 = 1.95.
        Since 2.0 > 1.95, effective_rr stays 2.0 (already sufficient).
        But the function still processes counter-trend logic.
        """
        from strategy.trading_logic import calculate_mechanical_sltp

        # For MEDIUM confidence: rr_target=2.0, counter_min=1.95, max(2.0, 1.95)=2.0
        # Both produce same TP because rr_target already exceeds counter_min
        _, _, tp_normal, _ = calculate_mechanical_sltp(
            entry_price=95000.0, atr_value=1200.0, side='LONG', confidence='MEDIUM',
            is_counter_trend=False,
        )
        _, _, tp_counter, _ = calculate_mechanical_sltp(
            entry_price=95000.0, atr_value=1200.0, side='LONG', confidence='MEDIUM',
            is_counter_trend=True,
        )
        # Both should be valid (same TP since 2.0 > 1.95)
        assert tp_normal > 95000.0
        assert tp_counter >= tp_normal  # >= because counter R/R can only increase

    def test_rr_target_honored(self, mock_config):
        """Verify that R/R target matches the config values."""
        from strategy.trading_logic import calculate_mechanical_sltp

        # HIGH: SL = 2.0×ATR = 2400, TP = SL × 2.5 = 6000
        success, sl, tp, desc = calculate_mechanical_sltp(
            entry_price=95000.0, atr_value=1200.0, side='LONG', confidence='HIGH',
        )
        assert success is True
        sl_dist = 95000.0 - sl     # Should be 2400
        tp_dist = tp - 95000.0     # Should be 6000
        rr = tp_dist / sl_dist
        assert abs(rr - 2.5) < 0.01  # R/R should be 2.5


class TestMechanicalSltpAtr4h:
    """v39.0: Test 4H ATR as primary SL/TP source."""

    def test_4h_atr_used_when_provided(self, mock_config):
        """When atr_4h > 0, SL should be based on 4H ATR, not 30M."""
        from strategy.trading_logic import calculate_mechanical_sltp

        # 30M ATR = 500, 4H ATR = 1500
        _, sl_30m, _, _ = calculate_mechanical_sltp(
            entry_price=95000.0, atr_value=500.0, side='LONG',
            confidence='MEDIUM', atr_4h=0.0,
        )
        _, sl_4h, _, _ = calculate_mechanical_sltp(
            entry_price=95000.0, atr_value=500.0, side='LONG',
            confidence='MEDIUM', atr_4h=1500.0,
        )
        # 4H ATR is 3× larger → SL should be further from entry
        sl_dist_30m = 95000.0 - sl_30m
        sl_dist_4h = 95000.0 - sl_4h
        assert sl_dist_4h > sl_dist_30m * 2.5  # ~3× wider

    def test_30m_fallback_when_4h_zero(self, mock_config):
        """When atr_4h = 0, should fall back to 30M ATR."""
        from strategy.trading_logic import calculate_mechanical_sltp

        success, sl, tp, desc = calculate_mechanical_sltp(
            entry_price=95000.0, atr_value=1200.0, side='LONG',
            confidence='HIGH', atr_4h=0.0,
        )
        assert success is True
        assert sl < 95000.0
        assert tp > 95000.0

    def test_both_atr_zero_returns_failure(self, mock_config):
        """When both ATRs are zero, should return failure."""
        from strategy.trading_logic import calculate_mechanical_sltp

        success, _, _, desc = calculate_mechanical_sltp(
            entry_price=95000.0, atr_value=0.0, side='LONG',
            confidence='HIGH', atr_4h=0.0,
        )
        assert success is False
        assert "No valid ATR" in desc

    def test_method_string_includes_atr_source(self, mock_config):
        """Method string should indicate ATR source (4H vs 30M)."""
        from strategy.trading_logic import calculate_mechanical_sltp

        _, _, _, desc_4h = calculate_mechanical_sltp(
            entry_price=95000.0, atr_value=500.0, side='LONG',
            confidence='MEDIUM', atr_4h=1500.0,
        )
        assert "atr_src=4H" in desc_4h

        _, _, _, desc_30m = calculate_mechanical_sltp(
            entry_price=95000.0, atr_value=500.0, side='LONG',
            confidence='MEDIUM', atr_4h=0.0,
        )
        assert "atr_src=30M" in desc_30m

    def test_negative_4h_atr_uses_fallback(self, mock_config):
        """Negative atr_4h should be treated as unavailable → 30M fallback."""
        from strategy.trading_logic import calculate_mechanical_sltp

        success, sl, _, desc = calculate_mechanical_sltp(
            entry_price=95000.0, atr_value=1200.0, side='LONG',
            confidence='MEDIUM', atr_4h=-100.0,
        )
        assert success is True
        assert "atr_src=30M" in desc


class TestPositionSizeAtr4h:
    """v39.0: Test that calculate_position_size risk clamp uses 4H ATR."""

    @pytest.fixture
    def full_config(self, mock_config):
        """Extend mock_config with position sizing keys needed by calculate_position_size."""
        mock_config.setdefault('min_notional_usdt', 5.0)
        mock_config.setdefault('min_notional_safety_margin', 1.05)
        mock_config.setdefault('quantity_adjustment_step', 0.001)
        mock_config.setdefault('position_sizing', {
            'method': 'ai_controlled',
            'max_single_trade_risk_pct': 0.02,
        })
        mock_config.setdefault('ai_position_control', {
            'max_position_usdt': 10000,
            'confidence_mapping': {'HIGH': 80, 'MEDIUM': 50, 'LOW': 30},
            'appetite_scale': {'AGGRESSIVE': 1.0, 'NORMAL': 0.8, 'CONSERVATIVE': 0.5},
        })
        mock_config.setdefault('max_position_ratio', 0.12)
        mock_config.setdefault('equity', 50000.0)
        mock_config.setdefault('leverage', 10)
        return mock_config

    def test_risk_clamp_uses_4h_atr(self, full_config):
        """Risk clamp should use 4H ATR when provided."""
        from strategy.trading_logic import calculate_position_size

        signal_data = {
            'signal': 'LONG', 'confidence': 'MEDIUM',
            'position_size_pct': 80, 'risk_appetite': 'NORMAL',
        }
        price_data = {'price': 95000.0}
        technical_data = {'atr': 500.0, 'rsi': 50, 'overall_trend': 'BULLISH'}

        # With 30M ATR only (small ATR → tight SL → large max_risk_usdt → less clamping)
        qty_30m, _ = calculate_position_size(
            signal_data, price_data, technical_data, full_config, atr_4h=0.0,
        )

        # With 4H ATR (large ATR → wide SL → small max_risk_usdt → more clamping)
        qty_4h, _ = calculate_position_size(
            signal_data, price_data, technical_data, full_config, atr_4h=1500.0,
        )

        # 4H ATR is 3× larger → risk clamp should be tighter → smaller position
        assert qty_4h <= qty_30m
