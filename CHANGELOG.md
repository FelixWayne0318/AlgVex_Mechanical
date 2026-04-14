# Changelog

All notable changes to AlgVex will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.5.0] - 2026-03-01

### Added - FR Consecutive Block Counter (v21.0)

**Dead Loop Prevention**: Tracks consecutive funding rate blocks in the same direction. When FR has blocked the same direction ≥3 consecutive times, the system degrades same-direction AI signals to HOLD, preventing futile repeated entries.

#### Core Feature

- **`_fr_consecutive_blocks` / `_fr_block_direction`** (`strategy/ai_strategy.py`)
  - Increments counter each time FR blocks the same direction
  - Resets on direction change or successful entry
  - Does NOT reset on position close (FR pressure is a market condition, not position state)
  - `fr_block_context` injected into AI data when count ≥ 2

#### AI Integration

- `_format_technical_report()` outputs FR block warning when count ≥ 2
- Indicator Reference v4.0 updated with FR BLOCKING guidance in Funding Rate section
- Telegram notification sent on signal degradation events

### Added - 1D Historical Context Time Series (v21.0)

**Trend Exhaustion Detection**: 10-bar 1D time series data injected into AI analysis for detecting ADX trajectory changes and DI spread evolution.

#### Core Feature

- **`trend_manager.get_historical_context(count=10)`** (`indicators/multi_timeframe_manager.py`)
  - Returns 10-bar history of ADX, DI+, DI-, RSI, Price for the 1D timeframe
  - Injected into `ai_technical_data['mtf_trend_layer']['historical_context']`

#### Technical Report

- `=== 1D TIME SERIES ===` section with auto-computed trend annotations
  - `[TREND: FALLING X%]` / `[TREND: RISING X%]` / `[TREND: Flat]`
  - Enables AI to detect ADX declining from peak (trend weakening) and DI convergence/divergence

---

## [2.4.0] - 2026-02-28

### Added - ATR Volatility Regime (v20.0)

**Volatility Environment Classification**: ATR% percentile ranking against rolling 90-bar historical distribution.

#### Core Feature

- **`_calculate_atr_regime()`** (`indicators/technical_manager.py`)
  - ATR% = ATR(14) / Price × 100
  - 4 regimes: LOW (<30th pctl), NORMAL (30-70th), HIGH (70-90th), EXTREME (>90th)
  - Orthogonal to ADX: ADX measures trend directionality, Vol Regime measures fluctuation magnitude
  - Pure RISK/CONTEXT signal — adjusts position sizing and stop width, not direction
  - SSoT: `classify_volatility_regime()` in `utils/shared_logic.py`
  - Thresholds are domain knowledge constants (30/70/90 percentiles), not configurable via YAML

#### Signal Confidence Matrix

- 3 new rows: `Vol LOW` (1.0-1.2), `Vol HIGH` (0.8), `Vol EXTREME` (0.6)
- ADX high + Vol LOW = orderly trend (ideal); ADX low + Vol HIGH = chaotic chop (worst)

### Added - OBV Divergence Detection (v20.0)

**Macro Volume Flow Analysis**: EMA(20)-smoothed OBV divergence detection complementary to CVD micro order flow.

#### Core Feature

- **`_ema_smooth()` + `_update_obv()`** (`indicators/technical_manager.py`)
  - OBV values incrementally accumulated in `_obv_values`
  - EMA(20) smoothing reduces 24/7 crypto market noise
  - Integrated into existing `_detect_divergences(obv_series=...)` infrastructure

#### Dual Timeframe Coverage

- **4H**: Primary OBV divergence detection
- **30M**: Supplementary OBV divergence detection
- OBV captures macro accumulation/distribution patterns
- CVD captures micro order flow aggressiveness (complementary, not redundant)

#### Signal Confidence Matrix

- `4H OBV divergence`: ADX>40=0.7 (trend overrides), ADX<20=1.2 (mean-reversion)
- `OBV+CVD confluence div`: ADX>40=1.1, ADX<20=1.4 (high confidence volume signal)
- OBV alone: 40-60% false positive rate — requires RSI/MACD/CVD confluence confirmation

---

## [2.3.0] - 2026-02-28

### Codebase Cleanup (Occam's Razor)

**Comprehensive system-wide update** to align all code and documentation with the current v19.2 system state.

#### Deleted (Occam's Razor)
- `configs/strategy_config.yaml` — unused legacy config (production uses `base.yaml` + env overlay)
- 6 `.backup` files — git history serves as backup
- `PLAN.md`, `PLAN_architecture_fixes*.md`, `WORKFLOW_FIX_README.md`, `workflow-fix.patch`, `CODEBASE_REVIEW.md` — completed planning docs
- `docs/architecture/ARCHITECTURE_ANALYSIS.md` — historical analysis from Nov 2025
- `docs/features/BAR_PERSISTENCE_IMPLEMENTATION.md` — superseded by current implementation
- `docs/features/FEATURE_TELEGRAM_REMOTE_CONTROL.md` — superseded by v14.0 dual-channel
- `docs/proposals/optimization_plan_v18.md` — completed planning doc (3300 lines)
- `docs/proposals/reflection-system-reform-v18.md` — implemented in v18.0
- `docs/proposals/architecture_v19.1_overview.md` — implemented in v19.1
- `docs/INDICATOR_CONFIDENCE_MATRIX.md.backup` — duplicate

#### Fixed
- **[HIGH] period_hours calculation bug** (`ai_strategy.py:3558`): Was `len(bars) * 15 / 60` (assumed 15M bars), now correctly `len(bars) * 30 / 60` for 30M execution layer (v18.2). Period hours were underestimated by 50%.
- **Default timeframe fallbacks** (`main_live.py`): Changed from `'15m'`/`'15-MINUTE-LAST'` to `'30m'`/`'30-MINUTE-LAST'`
- **Variable naming** (`multi_agent_analyzer.py`): Renamed `adx_15m` → `adx_30m` to match 30M execution layer
- **Default sentiment timeframe** (`ai_strategy.py`): Changed from `"15m"` to `"30m"`
- **Data assembler defaults** (`ai_data_assembler.py`): Changed interval defaults from `"15m"` to `"30m"`
- **diagnose_no_signal.py**: Fixed references to deleted `strategy_config.yaml` → now reads `base.yaml` with correct nested key paths
- **validate_data_pipeline.py**: Fixed hardcoded `15-MINUTE` bar type → `30-MINUTE`

#### Updated Documentation
- `CLAUDE.md`: Updated file structure to include all 20+ utils modules, indicators/multi_timeframe_manager.py, web backend/frontend details, diagnostics modules
- `docs/SYSTEM_OVERVIEW.md`: Updated to reflect v19.2 architecture
- `docs/DATA_SOURCES_MATRIX.md`: Updated to v7.0, all 13 data categories, 30M execution layer, removed obsolete "missing" sections
- `docs/setup/TELEGRAM_SETUP.md`: Rewritten for v14.0 dual-channel architecture
- `docs/troubleshooting/TELEGRAM_TROUBLESHOOTING.md`: Updated for `~/.env.algvex` and dual-channel
- `docs/development/GIT_WORKFLOW.md`: Fixed repo URL to FelixWayne0318/AlgVex, `python` → `python3`
- `docs/TESTING_GUIDE.md`: Fixed `python` → `python3`, service name `deepseek-trader` → `nautilus-trader`
- `docs/SECURITY.md`: Fixed service name
- `docs/INDICATOR_CONFIDENCE_MATRIX.md`: Fixed `15M` → `30M` reference
- `QUICKSTART.md`: Fixed `python` → `python3`, `15-minute` → `30-minute`
- `CHANGELOG.md`: Fixed `15M` execution layer reference in v2.1.0 entry
- Multiple stale comments referencing `strategy_config.yaml` or `15M` cleaned up

---

## [2.2.0] - 2026-02-28

### Added - ATR Extension Ratio (v19.1)

**Overextension Detection**: Volatility-normalized price displacement metric for AI risk assessment.

#### Core Feature

- **ATR Extension Ratio** (`indicators/technical_manager.py`)
  - Formula: `(Price - SMA) / ATR` — measures how far price has stretched from its moving average, normalized by current volatility
  - 4-regime classification: NORMAL (<2 ATR), EXTENDED (2-3), OVEREXTENDED (3-5), EXTREME (≥5)
  - Calculated for all SMA periods (5, 20, 50, 200), primary regime based on SMA20
  - Pure RISK signal — does not affect trade direction or mechanical SL/TP
  - Thresholds are domain knowledge constants (not configurable via YAML)

#### AI Integration (4 Agents)

- **Bull Analyst**: Extension ratio in Signal Audit checklist + entry condition assessment
- **Bear Analyst**: Extension ratio as pullback risk argument tool
- **Judge**: Extension ratio in entry_quality STEP 5 with regime-specific guidance
- **Risk Manager**: Extension ratio for position sizing guidance (OVEREXTENDED → reduce size)

#### Signal Confidence Matrix

- Added 2 new rows to `SIGNAL_CONFIDENCE_MATRIX`:
  - `Ext Ratio >3 (overextended)`: ADX>40=0.7, ADX25-40=1.0, ADX<20=1.3, SQUEEZE=0.8, VOLATILE=0.6
  - `Ext Ratio >5 (extreme)`: ADX>40=1.0, ADX25-40=1.2, ADX<20=1.4, SQUEEZE=0.9, VOLATILE=0.8

#### Technical Report

- Extension ratio displayed in PRICE & VOLATILITY section with warning labels for OVEREXTENDED/EXTREME
- 4H and 1D layers include inline extension ratio calculations

### Added - RSI/MACD Divergence Pre-computation (v19.1)

**Automated Divergence Detection**: Pre-computed divergence annotations injected directly into technical report, eliminating AI hallucination of divergences.

#### Core Feature

- **`_detect_divergences()`** (`agents/multi_agent_analyzer.py`)
  - Classic bearish divergence: price higher high + RSI/MACD lower high (momentum weakening)
  - Classic bullish divergence: price lower low + RSI/MACD higher low (selling exhaustion)
  - Local extremes detection with window=2, indicator peak matching within ±2 bars
  - Minimum 5 data points required, graceful handling of missing/mismatched series

#### Dual Timeframe Coverage

- **4H Divergence**: Decision-layer divergence detection (RSI + MACD Hist)
  - Output: `=== 4H DIVERGENCE DETECTION (pre-computed) ===`
- **30M Divergence**: Execution-layer divergence detection (RSI + MACD Hist)
  - Output: `30M DIVERGENCE DETECTION (pre-computed, entry timing only):`

### Added - CVD-Price Cross-Analysis (v19.1)

**Order Flow Divergence**: Automatic cross-analysis between price movement and Cumulative Volume Delta.

#### Detection Logic

- **ACCUMULATION**: Price falling (< -0.3%) + CVD net positive → "smart money buying the dip"
- **DISTRIBUTION**: Price rising (> +0.3%) + CVD net negative → "rally on weak buying"
- **CONFIRMED**: Price falling + CVD negative → confirmed selling pressure
- Thresholds: `|price_change| > 0.3%`, CVD history ≥ 3 bars, net CVD from last 5 bars

#### Dual Layer Integration

- **30M Order Flow Report**: CVD-Price tag appended to order flow section
- **4H Order Flow**: Independent 4H CVD-Price divergence detection in technical report

#### Verification

- `scripts/verify_extension_ratio.py`: 4-phase, 47-check verification suite
  - Phase 1: 10 calculation tests (pure math, edge cases)
  - Phase 2: 15 source code integration checks
  - Phase 3: 15 regime boundary tests
  - Phase 4: 7 real-world BTC scenarios

### Design Decisions

- Extension ratio is orthogonal to `calculate_mechanical_sltp()` — SL/TP remains ATR × confidence multiplier
- No Mayer Multiple (Price/SMA200): fixed thresholds become unreliable as markets mature; ATR normalization adapts to volatility
- No config parameters: 2/3/5 ATR thresholds are established domain knowledge, not tunable knobs
- Divergences are pre-computed as annotations, not left for AI to detect (reduces hallucination risk)
- CVD-Price analysis uses conservative thresholds (0.3% price change, 3+ bars) to minimize false signals

---

## [2.1.0] - 2026-01-27

### Added - Multi-Timeframe Framework (MTF)

**Major Architecture Enhancement**: Three-layer decision framework based on [TradingAgents](https://github.com/TauricResearch/TradingAgents) (UCLA/MIT research).

#### Core Features

- **Multi-Timeframe Analysis**
  - Trend Layer (1D): Risk-On/Risk-Off market regime filter
    - SMA_200 for long-term trend identification
    - MACD for trend strength confirmation
    - Blocks all trades during bearish macro trends
  - Decision Layer (4H): Bull/Bear debate with quantitative Judge framework
    - Bull Analyst: Persuasive bullish arguments (temperature=0.3)
    - Bear Analyst: Skeptical bearish arguments (temperature=0.3)
    - Judge: Confirmation counting algorithm (temperature=0.1)
    - Reduces subjective HOLD bias through algorithmic decision rules
  - Execution Layer (30M): Precise entry timing (migrated from 15M in v18.2)
    - RSI entry range validation (35-65)
    - Support/resistance-based stop loss placement
    - Exact entry point determination

#### Data Source Enhancements

- **Order Flow Analysis** (from Binance K-line complete fields)
  - Buy/Sell Ratio: Measures buying vs selling pressure
    - Bullish threshold: >55% buy volume
    - Bearish threshold: <45% buy volume
  - CVD (Cumulative Volume Delta): Tracks net money flow direction
    - RISING: Sustained buying pressure
    - FALLING: Sustained selling pressure
    - NEUTRAL: Balanced market
  - Average Trade Size: Identifies institutional vs retail activity
  - 10-bar rolling window: Short-term trend confirmation

- **Derivatives Data Integration** (via Coinalyze API)
  - Open Interest (OI): Position accumulation indicator
    - +5% change = strong trend confirmation
    - -5% change = potential trend exhaustion
  - Funding Rate: Market sentiment and squeeze risk
    - >0.01% = overleveraged longs (bearish signal)
    - <-0.01% = overleveraged shorts (bullish signal)
  - Liquidations (1-hour): Extreme market stress indicator
    - High liquidations = capitulation signal
    - Direction indicates which side is getting squeezed

#### Technical Implementation

- **New Modules**
  - `utils/binance_kline_client.py`: Multi-timeframe K-line data fetcher
  - `utils/order_flow_processor.py`: Buy/sell ratio and CVD calculator
  - `utils/coinalyze_client.py`: Derivatives data client with fallback
  - `utils/ai_data_assembler.py`: Unified data aggregation layer

- **Enhanced Modules**
  - `agents/multi_agent_analyzer.py`:
    - Added `order_flow_report` parameter to `analyze()` method
    - Added `derivatives_report` parameter to `analyze()` method
    - New `_format_order_flow_report()` for AI prompt integration
    - New `_format_derivatives_report()` for derivatives context
  - `strategy/ai_strategy.py`:
    - MTF framework integration in `on_timer()`
    - Multi-timeframe bar routing
    - Enhanced AI context with 4 data sources

#### Configuration

- **New Config Sections** (`configs/base.yaml`)
  - `multi_timeframe.*`: Three-layer framework configuration
    - `trend_layer.*`: 1D trend filter settings
    - `decision_layer.*`: 4H debate configuration
    - `execution_layer.*`: 15M entry timing parameters
  - `order_flow.*`: Order flow analysis settings
    - `binance.*`: K-line field selection
    - `buy_ratio.*`: Bullish/bearish thresholds
  - `coinalyze.*`: Derivatives data configuration
    - `endpoints.*`: OI/Funding/Liquidations toggle
    - `fallback_*`: Graceful degradation defaults

- **Environment Variables**
  - `COINALYZE_API_KEY`: Optional API key for derivatives data
    - System works without it (uses order flow + technical only)
    - Get key at: https://coinalyze.net/

#### Robustness Improvements

- **Complete Degradation Strategy**
  - Priority 1: RISK_OFF filter (trend layer blocks bearish markets)
  - Priority 2: Decision state matching (prevents counter-signal trades)
  - Priority 3: RSI confirmation (avoids overbought/oversold entries)
  - API Failures: Graceful fallback to neutral default values
  - Data Staleness: Automatic invalidation and fallback

- **Data Validation**
  - Binance K-line format compatibility (both NautilusTrader and raw formats)
  - Coinalyze API response validation with error handling
  - Freshness checks for all external data sources

#### Performance Optimizations

- Synchronous architecture (no asyncio overhead)
- Cached trend layer state (4-hour TTL, reduces API calls)
- Efficient data aggregation (single pass through AI Data Assembler)
- Smart request batching for Coinalyze endpoints

#### Expected Improvements

- **Signal Quality**: Order flow confirms real trade intent, reduces false breakouts
- **Risk Management**: Derivatives data warns of liquidation cascades and funding squeezes
- **Decision Efficiency**: Judge confirmation counting reduces HOLD bias from 40% to <30%
- **Market Regime Awareness**: Trend filter prevents counter-trend disasters

### Changed

- **AI Temperature Settings Clarification**
  - Bull/Bear Analysts: 0.3 (need debate diversity)
  - Judge/Risk Manager: 0.1 (need deterministic logic)
  - Documented rationale in CLAUDE.md

- **TradingAgents Architecture Evolution**
  - Original: Parallel signal merging (DeepSeek + MultiAgent)
  - v2.1: Hierarchical decision (MultiAgent Judge is final authority)
  - Eliminated signal fusion logic (legacy `skip_on_divergence`, `use_confidence_fusion`)

### Documentation

- **New Documents**
  - `CHANGELOG.md`: Version history (this file)

- **Updated Documents**
  - `CLAUDE.md`: Added MTF configuration section with examples
  - `README.md`: Updated architecture diagrams with MTF components
  - `configs/base.yaml`: Comprehensive MTF parameter documentation

### Technical Debt Addressed

- P0: Interface signature mismatch in `MultiAgentAnalyzer.analyze()` - ✅ Fixed
- P1: Order flow formatting methods missing - ✅ Implemented
- P2: Coinalyze client not implemented - ✅ Completed with fallback

### Deployment Notes

- **Backward Compatible**: MTF can be disabled via `multi_timeframe.enabled: false`
- **Gradual Rollout Recommended**:
  - Week 1: Enable `order_flow.enabled: true` only
  - Week 2: Add `coinalyze.enabled: true` (if API key available)
  - Week 3: Full MTF with `multi_timeframe.enabled: true`
- **Validation Scripts**:
  - `python3 scripts/smart_commit_analyzer.py`: Regression detection

### Credits

- MTF Architecture: Based on [TradingAgents Framework](https://github.com/TauricResearch/TradingAgents) by Tauric Research (UCLA/MIT)
- Order Flow Concepts: Adapted from institutional trading practices
- Derivatives Integration: Powered by [Coinalyze API](https://coinalyze.net/)

---

## [1.2.2] - 2025-11-15

### Fixed

- **Bracket Order Emulation**: Corrected order emulation flow for Binance
- **Telegram Sync Error**: Fixed event loop error in `send_message_sync()`
- **OCO Error Handling**: Improved error handling in OCO manager

### Improved

- Bracket order documentation and flow diagrams
- Error messages for common configuration issues

---

## [1.2.0] - 2025-10-20

### Added

- **Partial Take Profit System**
  - Multi-level profit-taking (e.g., 50% at +2%, 50% at +4%)
  - Configurable thresholds and position percentages
  - Risk reduction while maintaining upside potential

- **Trailing Stop Loss**
  - Dynamic stop loss that follows price
  - Activation threshold (default: +1% profit)
  - Trailing distance (default: 0.5%)
  - Update threshold to reduce order spam

- **Telegram Remote Control**
  - `/status`: System status and balance
  - `/position`: Current positions
  - `/orders`: Active orders
  - `/pause` / `/resume`: Trading control
  - `/close`: Emergency position closure

- **OCO (One-Cancels-the-Other) Management**
  - Redis persistence for OCO groups
  - Automatic peer order cancellation
  - Orphan order cleanup
  - Survives strategy restarts

- **Bracket Orders**
  - Native NautilusTrader bracket order support
  - Simultaneous SL/TP with entry order
  - Order emulation for unsupported exchanges

---

## [1.1.0] - 2025-09-10

### Added

- **Automated Stop Loss & Take Profit**
  - Support/resistance-based stop loss calculation
  - Configurable buffer percentage (default: 0.1%)
  - AI confidence-based take profit targets:
    - HIGH: 3% profit target
    - MEDIUM: 2% profit target
    - LOW: 1% profit target
  - STOP_MARKET for stop loss
  - LIMIT orders for take profit

### Improved

- Risk management framework
- Position sizing logic
- Error handling for order placement

---

## [1.0.0] - 2025-08-01

### Added

- **Core Trading System**
  - DeepSeek AI integration for signal generation
  - NautilusTrader framework migration from original implementation
  - Binance Futures support (BTCUSDT-PERP)
  - Event-driven architecture

- **Technical Analysis**
  - Moving Averages: SMA (5, 20, 50), EMA (12, 26)
  - Momentum: RSI (14), MACD (12, 26, 9)
  - Volatility: Bollinger Bands (20, 2σ)
  - Support/Resistance detection
  - Volume analysis

- **Sentiment Integration**
  - Binance Long/Short Ratio API
  - Real-time market sentiment analysis
  - Weighted sentiment in AI decision-making

- **Position Sizing**
  - AI confidence-based multipliers
  - Trend strength adjustments
  - RSI extreme adjustments
  - Maximum position ratio enforcement

- **Configuration Management**
  - Multi-environment support (production/development/backtest)
  - Centralized configuration via ConfigManager
  - Environment variable integration

### Documentation

- Initial README with quick start guide
- Deployment documentation (DEPLOYMENT.md)
- Architecture overview
- Risk disclaimers

---

## Version Numbering

- **Major.Minor.Patch** (Semantic Versioning)
  - **Major**: Breaking changes or major architecture overhauls
  - **Minor**: New features, non-breaking enhancements
  - **Patch**: Bug fixes, documentation updates

## Links

- [GitHub Repository](https://github.com/FelixWayne0318/AlgVex)
- [TradingAgents Framework](https://github.com/TauricResearch/TradingAgents)
- [NautilusTrader Documentation](https://nautilustrader.io/)
- [DeepSeek AI](https://www.deepseek.com/)

---

**For detailed technical documentation, see:**
- `CLAUDE.md` - Development guidelines and configuration
- `README.md` - User guide and quick start
- `docs/SYSTEM_OVERVIEW.md` - Complete architecture reference
