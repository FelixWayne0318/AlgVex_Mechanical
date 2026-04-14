"""
Prometheus Metrics Exporter for AlgVex trading system.

Exposes trading, AI quality, data pipeline, and system metrics
for Grafana dashboards.

Reference: docs/upgrade_plan_v2/08_EXECUTION_INFRA.md §Phase 2B

Usage:
  # Start metrics server (called from main_live.py when prometheus.enabled=true)
  from utils.metrics_exporter import MetricsExporter
  exporter = MetricsExporter(port=9090)
  exporter.start()

  # Update metrics during operation
  exporter.record_trade(side="LONG", pnl_pct=1.5, confidence="HIGH")
  exporter.record_ai_call(agent="Judge", duration_ms=5200, tokens=9000)
  exporter.update_position(equity=10000, drawdown_pct=3.5, regime="TRENDING_UP")
"""

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from prometheus_client import (
        start_http_server,
        Counter,
        Gauge,
        Histogram,
        Summary,
    )
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _PROMETHEUS_AVAILABLE = False
    logger.info("prometheus_client not installed — metrics disabled")


class MetricsExporter:
    """Prometheus metrics exporter with lazy initialization.

    All methods are no-ops if prometheus_client is not installed
    or prometheus.enabled=false.
    """

    def __init__(self, config: Optional[dict] = None):
        cfg = (config or {}).get("prometheus", {})
        self._enabled = cfg.get("enabled", False) and _PROMETHEUS_AVAILABLE
        self._port = cfg.get("port", 9090)
        self._started = False

        if not self._enabled:
            return

        # ── Trading Metrics ──
        self.trades_total = Counter(
            "algvex_trades_total",
            "Total trades executed",
            ["side", "confidence"],
        )
        self.trade_pnl = Histogram(
            "algvex_trade_pnl_pct",
            "Trade PnL percentage",
            buckets=[-5, -3, -2, -1, -0.5, 0, 0.5, 1, 2, 3, 5, 10],
        )
        self.win_rate = Gauge(
            "algvex_win_rate",
            "Rolling win rate (last 20 trades)",
        )
        self.current_drawdown = Gauge(
            "algvex_drawdown_pct",
            "Current drawdown percentage",
        )
        self.equity = Gauge(
            "algvex_equity_usd",
            "Current account equity (USD)",
        )
        self.position_size = Gauge(
            "algvex_position_size_pct",
            "Current position size percentage",
        )

        # ── AI Metrics ──
        self.ai_calls_total = Counter(
            "algvex_ai_calls_total",
            "Total AI API calls",
            ["agent"],
        )
        self.ai_call_duration = Histogram(
            "algvex_ai_call_duration_seconds",
            "AI API call duration",
            ["agent"],
            buckets=[5, 10, 20, 30, 60, 90, 120, 180],
        )
        self.ai_tokens_total = Counter(
            "algvex_ai_tokens_total",
            "Total AI tokens consumed",
            ["agent", "type"],  # type: input/output
        )
        self.ai_quality_score = Gauge(
            "algvex_ai_quality_score",
            "AI Quality Auditor score (0-100)",
        )
        self.signal_count = Counter(
            "algvex_signals_total",
            "Total signals generated",
            ["signal"],  # LONG/SHORT/HOLD
        )

        # ── Data Pipeline Metrics ──
        self.data_fetch_errors = Counter(
            "algvex_data_fetch_errors_total",
            "Data fetch errors by source",
            ["source"],
        )
        self.data_validation_warnings = Counter(
            "algvex_data_validation_warnings_total",
            "Data validation warnings (Pandera)",
            ["tier"],  # tier1/tier2/tier3
        )

        # ── System Metrics ──
        self.regime = Gauge(
            "algvex_market_regime",
            "Current market regime (encoded: 1=TRENDING_UP, 2=TRENDING_DOWN, 3=RANGING, 4=HIGH_VOL)",
        )
        self.fear_greed = Gauge(
            "algvex_fear_greed_index",
            "Fear & Greed Index (0-100)",
        )
        self.trading_state = Gauge(
            "algvex_trading_state",
            "Trading state (1=ACTIVE, 2=REDUCED, 3=HALTED, 4=COOLDOWN)",
        )

    def start(self) -> None:
        """Start the Prometheus HTTP server in a background thread."""
        if not self._enabled or self._started:
            return
        try:
            start_http_server(self._port)
            self._started = True
            logger.info(f"Prometheus metrics server started on port {self._port}")
        except Exception as e:
            logger.warning(f"Failed to start Prometheus server: {e}")

    # ── Recording Methods ──

    def record_trade(self, side: str, pnl_pct: float, confidence: str) -> None:
        if not self._enabled:
            return
        self.trades_total.labels(side=side, confidence=confidence).inc()
        self.trade_pnl.observe(pnl_pct)

    def record_ai_call(self, agent: str, duration_ms: float,
                       input_tokens: int = 0, output_tokens: int = 0) -> None:
        if not self._enabled:
            return
        self.ai_calls_total.labels(agent=agent).inc()
        self.ai_call_duration.labels(agent=agent).observe(duration_ms / 1000.0)
        if input_tokens:
            self.ai_tokens_total.labels(agent=agent, type="input").inc(input_tokens)
        if output_tokens:
            self.ai_tokens_total.labels(agent=agent, type="output").inc(output_tokens)

    def record_signal(self, signal: str) -> None:
        if not self._enabled:
            return
        self.signal_count.labels(signal=signal).inc()

    def update_position(self, equity: float = 0, drawdown_pct: float = 0,
                        regime: str = "", quality_score: float = 0,
                        fear_greed: int = 0, trading_state: str = "ACTIVE") -> None:
        if not self._enabled:
            return
        if equity > 0:
            self.equity.set(equity)
        self.current_drawdown.set(drawdown_pct)
        if quality_score > 0:
            self.ai_quality_score.set(quality_score)
        if fear_greed > 0:
            self.fear_greed.set(fear_greed)

        regime_map = {"TRENDING_UP": 1, "TRENDING_DOWN": 2, "RANGING": 3,
                      "HIGH_VOLATILITY": 4, "STRONG_TREND": 1, "WEAK_TREND": 2}
        self.regime.set(regime_map.get(regime, 3))

        state_map = {"ACTIVE": 1, "REDUCED": 2, "HALTED": 3, "COOLDOWN": 4}
        self.trading_state.set(state_map.get(trading_state, 1))

    def record_data_error(self, source: str) -> None:
        if not self._enabled:
            return
        self.data_fetch_errors.labels(source=source).inc()

    def record_validation_warning(self, tier: str) -> None:
        if not self._enabled:
            return
        self.data_validation_warnings.labels(tier=tier).inc()

    @property
    def available(self) -> bool:
        return self._enabled
