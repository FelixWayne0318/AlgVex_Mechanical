"""Tests for Pandera data validation (v2.0)."""
import pytest
from utils.data_validator import validate_features


class TestFeatureValidation:
    def test_valid_features_returns_list(self):
        features = {"rsi_30m": 50.0, "adx_4h": 25.0, "market_regime": "RANGING"}
        warnings = validate_features(features)
        assert isinstance(warnings, list)

    def test_rsi_out_of_range(self):
        features = {"rsi_30m": 150.0, "current_price": 65000}
        warnings = validate_features(features)
        assert any("rsi_30m" in w for w in warnings)

    def test_invalid_enum(self):
        features = {"market_regime": "INVALID", "current_price": 65000}
        warnings = validate_features(features)
        assert len(warnings) > 0
