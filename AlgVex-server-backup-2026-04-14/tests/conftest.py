"""
AlgVex Test Configuration and Shared Fixtures.

Provides reusable fixtures for testing core trading components
without requiring NautilusTrader runtime or live API connections.
"""
import sys
from pathlib import Path
from unittest.mock import Mock, MagicMock
import pytest
import yaml
import tempfile
import os

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def project_root():
    """Return the project root directory."""
    return PROJECT_ROOT


@pytest.fixture
def base_config_dict():
    """Load and return the base.yaml config as a dict."""
    config_path = PROJECT_ROOT / "configs" / "base.yaml"
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


@pytest.fixture
def config_manager(tmp_path):
    """Create a ConfigManager instance with test configs."""
    from utils.config_manager import ConfigManager

    # Copy base.yaml to temp dir
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    base_src = PROJECT_ROOT / "configs" / "base.yaml"
    base_dst = config_dir / "base.yaml"
    base_dst.write_text(base_src.read_text(encoding='utf-8'), encoding='utf-8')

    # Create minimal production.yaml
    prod_yaml = config_dir / "production.yaml"
    prod_yaml.write_text("# test production override\n", encoding='utf-8')

    mgr = ConfigManager(config_dir=config_dir, env="production")
    mgr.load()
    return mgr


@pytest.fixture
def mock_logger():
    """Create a mock logger with all standard methods."""
    logger = Mock()
    logger.info = Mock()
    logger.debug = Mock()
    logger.warning = Mock()
    logger.error = Mock()
    logger.critical = Mock()
    return logger


@pytest.fixture
def sample_technical_data():
    """Sample technical indicator data for testing."""
    return {
        'current_price': 95000.0,
        'sma_5': 94500.0,
        'sma_20': 93000.0,
        'sma_50': 91000.0,
        'rsi': 55.0,
        'macd': 150.0,
        'macd_signal': 100.0,
        'bb_upper': 97000.0,
        'bb_lower': 91000.0,
        'bb_middle': 94000.0,
        'bb': 66.7,
        'adx': 28.0,
        'atr': 1200.0,
        'volume_ratio': 1.2,
        'trend': 'BULLISH',
    }


@pytest.fixture
def sample_memory_entry():
    """Sample trading memory entry for testing."""
    return {
        'timestamp': '2026-02-20T12:00:00+00:00',
        'action': 'LONG',
        'entry_price': 94000.0,
        'exit_price': 95500.0,
        'pnl_pct': 1.6,
        'pnl_usdt': 16.0,
        'confidence': 'HIGH',
        'grade': 'A',
        'lesson': 'Strong trend continuation with volume confirmation.',
        'sl_price': 93000.0,
        'tp_price': 96000.0,
        'duration_hours': 4.5,
        'market_conditions': {
            'trend': 'BULLISH',
            'rsi': 55,
            'bb': 65,
            'funding_rate': 0.0001,
        },
        'reflection': 'Entry was well-timed with SMA crossover confirmation.',
    }


@pytest.fixture
def trading_logic_config():
    """Return the trading_logic section of config for testing."""
    config_path = PROJECT_ROOT / "configs" / "base.yaml"
    with open(config_path, 'r', encoding='utf-8') as f:
        full_config = yaml.safe_load(f)
    return full_config.get('trading_logic', {})
