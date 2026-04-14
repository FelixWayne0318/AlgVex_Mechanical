"""
Feature Schema & Reason Tags Verification Tests

Tests:
1. FEATURE_SCHEMA completeness — all keys have valid types
2. REASON_TAGS validation — integrity and category coverage
"""

import pytest
from agents.prompt_constants import (
    FEATURE_SCHEMA,
    REASON_TAGS,
)


class TestFeatureSchemaCompleteness:
    """Verify all FEATURE_SCHEMA keys have correct structure."""

    def test_feature_schema_has_required_count(self):
        """Schema should have 80+ features."""
        assert len(FEATURE_SCHEMA) >= 80, f"Only {len(FEATURE_SCHEMA)} features defined"

    def test_all_features_have_type(self):
        """Every feature must declare a type."""
        for key, spec in FEATURE_SCHEMA.items():
            assert "type" in spec, f"Feature '{key}' missing 'type'"

    def test_feature_types_valid(self):
        """Feature types must be float, int, enum, or bool."""
        valid_types = {"float", "int", "enum", "bool"}
        for key, spec in FEATURE_SCHEMA.items():
            assert spec["type"] in valid_types, (
                f"Feature '{key}' has invalid type '{spec['type']}'"
            )

    def test_enum_features_have_values(self):
        """Enum features must declare valid values."""
        for key, spec in FEATURE_SCHEMA.items():
            if spec["type"] == "enum":
                assert "values" in spec, f"Enum feature '{key}' missing 'values'"
                assert len(spec["values"]) >= 2, (
                    f"Enum feature '{key}' needs >=2 values"
                )


class TestReasonTags:
    """Verify REASON_TAGS integrity."""

    def test_reason_tags_has_required_count(self):
        """Should have 75+ tags."""
        assert len(REASON_TAGS) >= 75, f"Only {len(REASON_TAGS)} tags defined"

    def test_all_tags_uppercase(self):
        """Tags must be uppercase (convention for enum-like values)."""
        for tag in REASON_TAGS:
            assert tag == tag.upper(), f"Tag '{tag}' is not uppercase"

    def test_no_duplicate_tags(self):
        """Tags must be unique (set enforces this, but verify)."""
        tag_list = list(REASON_TAGS)
        assert len(tag_list) == len(set(tag_list))

    def test_tag_categories_present(self):
        """Key tag categories must be represented."""
        categories = {
            "trend": ["TREND_1D_BULLISH", "TREND_1D_BEARISH"],
            "momentum": ["RSI_OVERBOUGHT", "RSI_OVERSOLD"],
            "order_flow": ["CVD_POSITIVE", "CVD_NEGATIVE"],
            "derivatives": ["FR_FAVORABLE_LONG", "FR_FAVORABLE_SHORT"],
            "risk": ["EXTENSION_OVEREXTENDED", "EXTENSION_EXTREME"],
            "memory": ["LATE_ENTRY", "TREND_ALIGNED", "SL_TOO_TIGHT"],
        }
        for cat, tags in categories.items():
            for tag in tags:
                assert tag in REASON_TAGS, f"Missing {cat} tag: {tag}"

    def test_reason_tags_in_output_validation(self):
        """Verify tag filtering logic matches REASON_TAGS."""
        sample_output = {
            "decisive_reasons": [
                "TREND_1D_BULLISH",     # valid
                "MACD_BULLISH_CROSS",   # valid
                "INVALID_TAG_XYZ",      # should be filtered
            ]
        }
        valid = [t for t in sample_output["decisive_reasons"] if t in REASON_TAGS]
        invalid = [t for t in sample_output["decisive_reasons"] if t not in REASON_TAGS]
        assert len(valid) == 2
        assert len(invalid) == 1
        assert "INVALID_TAG_XYZ" in invalid
