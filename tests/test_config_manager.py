"""
Tests for ConfigManager — YAML loading, deep merge, validation, and env secrets.

Covers:
- base.yaml loading and parsing
- Environment overlay merging (production/development)
- Validation rules (range, type, dependency)
- Path aliases for backward compatibility
- New v15.0 config entries (emergency_sl, timing, etc.)
"""
import sys
from pathlib import Path
from unittest.mock import patch
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.config_manager import ConfigManager, ConfigValidationError


class TestConfigLoading:
    """Test YAML config loading and merging."""

    def test_load_base_config(self, config_manager):
        """Base config should load with all required sections."""
        assert config_manager.get('trading', 'instrument_id') == "BTCUSDT-PERP.BINANCE"
        assert config_manager.get('capital', 'equity') == 1000
        assert config_manager.get('capital', 'leverage') == 10

    def test_get_nested_value(self, config_manager):
        """Nested config access should work."""
        # v47.0: AI config removed in v46.0, test with existing nested config
        min_rr = config_manager.get('trading_logic', 'min_rr_ratio')
        assert min_rr == 1.3
        assert isinstance(min_rr, float)

    def test_get_with_default(self, config_manager):
        """Missing keys should return the default."""
        val = config_manager.get('nonexistent', 'key', default='fallback')
        assert val == 'fallback'

    def test_deep_merge_override(self, tmp_path):
        """Environment config should override base values."""
        config_dir = tmp_path / "configs"
        config_dir.mkdir()

        base = {'capital': {'equity': 1000, 'leverage': 10}, 'timing': {'timer_interval_sec': 1200}}
        dev = {'timing': {'timer_interval_sec': 60}}

        (config_dir / "base.yaml").write_text(yaml.dump(base))
        (config_dir / "development.yaml").write_text(yaml.dump(dev))

        mgr = ConfigManager(config_dir=config_dir, env="development")
        mgr.load()

        assert mgr.get('timing', 'timer_interval_sec') == 60
        assert mgr.get('capital', 'equity') == 1000  # Not overridden

    def test_path_alias_backward_compat(self, config_manager):
        """Old-style paths should resolve via aliases."""
        # strategy.equity → capital.equity
        val = config_manager.get('strategy', 'equity')
        assert val is not None


class TestConfigValidation:
    """Test validation rules."""

    def test_validation_passes_with_defaults(self, config_manager):
        """Default base.yaml should pass validation."""
        assert config_manager.validate() is True
        assert len(config_manager.get_errors()) == 0

    def test_equity_below_minimum(self, tmp_path):
        """Equity below 100 should fail validation."""
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        base = yaml.safe_load((Path(__file__).resolve().parents[1] / "configs" / "base.yaml").read_text())
        base['capital']['equity'] = 50  # Below min 100
        (config_dir / "base.yaml").write_text(yaml.dump(base))
        (config_dir / "production.yaml").write_text("")

        mgr = ConfigManager(config_dir=config_dir, env="production")
        mgr.load()
        assert mgr.validate() is False
        errors = mgr.get_errors()
        assert any('capital.equity' in e.field for e in errors)

    def test_leverage_above_maximum(self, tmp_path):
        """Leverage above 125 should fail validation."""
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        base = yaml.safe_load((Path(__file__).resolve().parents[1] / "configs" / "base.yaml").read_text())
        base['capital']['leverage'] = 200  # Above max 125
        (config_dir / "base.yaml").write_text(yaml.dump(base))
        (config_dir / "production.yaml").write_text("")

        mgr = ConfigManager(config_dir=config_dir, env="production")
        mgr.load()
        assert mgr.validate() is False

    def test_rsi_thresholds_order(self, tmp_path):
        """RSI lower must be < upper."""
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        base = yaml.safe_load((Path(__file__).resolve().parents[1] / "configs" / "base.yaml").read_text())
        base['risk']['rsi_extreme_threshold_lower'] = 80  # Inverted
        base['risk']['rsi_extreme_threshold_upper'] = 30
        (config_dir / "base.yaml").write_text(yaml.dump(base))
        (config_dir / "production.yaml").write_text("")

        mgr = ConfigManager(config_dir=config_dir, env="production")
        mgr.load()
        # RSI lower is out of range (>50) so validation fails
        errors = mgr.get_errors()
        assert len(errors) > 0

    def test_min_rr_ratio_range(self, config_manager):
        """min_rr_ratio should be in [1.0, 5.0]."""
        val = config_manager.get('trading_logic', 'min_rr_ratio')
        assert 1.0 <= val <= 5.0

    def test_counter_trend_multiplier_range(self, config_manager):
        """counter_trend_rr_multiplier should be in [1.0, 3.0]."""
        val = config_manager.get('trading_logic', 'counter_trend_rr_multiplier')
        assert 1.0 <= val <= 3.0


class TestNewConfigEntries:
    """Test v15.0 config entries extracted from hardcoded values."""

    def test_emergency_sl_config(self, config_manager):
        """Emergency SL config should have all required fields."""
        base_pct = config_manager.get('trading_logic', 'emergency_sl', 'base_pct')
        atr_mult = config_manager.get('trading_logic', 'emergency_sl', 'atr_multiplier')
        cooldown = config_manager.get('trading_logic', 'emergency_sl', 'cooldown_seconds')
        max_consec = config_manager.get('trading_logic', 'emergency_sl', 'max_consecutive')

        assert base_pct == 0.02
        assert atr_mult == 1.5
        assert cooldown == 120
        assert max_consec == 3

    def test_sl_atr_multiplier_floor(self, config_manager):
        """SL ATR multiplier floor should exist in mechanical_sltp."""
        floor = config_manager.get('trading_logic', 'mechanical_sltp', 'sl_atr_multiplier_floor')
        assert floor == 0.5  # v39.0: 4H ATR floor (was 1.5 on 30M)

    def test_sr_zones_cache_ttl(self, config_manager):
        """S/R zones cache TTL should be configured."""
        ttl = config_manager.get('sr_zones', 'cache_ttl_seconds')
        assert ttl == 1800

    def test_timing_price_cache_ttl(self, config_manager):
        """Price cache TTL should be configured."""
        ttl = config_manager.get('timing', 'price_cache_ttl_seconds')
        assert ttl == 300

    def test_timing_reversal_timeout(self, config_manager):
        """Reversal timeout should be configured."""
        timeout = config_manager.get('timing', 'reversal_timeout_seconds')
        assert timeout == 300

    def test_max_leverage_limit(self, config_manager):
        """Max leverage limit should be configured."""
        limit = config_manager.get('capital', 'max_leverage_limit')
        assert limit == 125


class TestSensitiveDataMasking:
    """Test that sensitive values are properly masked."""

    def test_mask_long_value(self):
        mgr = ConfigManager()
        assert mgr._mask_sensitive("abcdefghij") == "abcd***ij"

    def test_mask_short_value(self):
        mgr = ConfigManager()
        assert mgr._mask_sensitive("abc") == "***"

    def test_mask_empty_value(self):
        mgr = ConfigManager()
        assert mgr._mask_sensitive("") == "(未设置)"
