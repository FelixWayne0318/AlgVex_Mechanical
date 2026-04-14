"""Tests for HMM RegimeDetector (v2.0)."""
import pytest
from utils.regime_detector import RegimeDetector

class TestRegimeDetector:
    def test_adx_fallback_strong(self):
        rd = RegimeDetector(config={"enabled": True})
        result = rd.predict({"adx_4h": 45, "rsi_4h": 55})
        assert result["source"] == "adx_fallback"
        assert result["regime"] == "STRONG_TREND"

    def test_adx_fallback_weak(self):
        rd = RegimeDetector(config={"enabled": True})
        result = rd.predict({"adx_4h": 15, "rsi_4h": 50})
        assert result["regime"] == "WEAK_TREND"

    def test_empty_features(self):
        rd = RegimeDetector(config={"enabled": True})
        result = rd.predict({})
        assert result["regime"] in ("RANGING", "WEAK_TREND", "STRONG_TREND")
