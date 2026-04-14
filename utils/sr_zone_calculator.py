# utils/sr_zone_calculator.py
"""
Support/Resistance Zone Calculator v3.1

职责:
- 聚合多个数据源的 S/R 候选价位
- 聚类形成 S/R Zone (价差 < cluster_pct 的合并)
- 计算 Zone 强度 (基于 confluence + touch count)
- 输出给 AI 和本地硬风控使用
- v2.0: 添加 level (时间框架级别) 和 source_type (来源类型)
- v2.0: 添加详细 AI 报告，包含原始数据供 AI 验证
- v3.0: 添加 Swing Point 检测 (Williams Fractal N-bar pivot)
- v3.0: ATR 自适应聚类阈值
- v3.0: Touch Count 评分 (2-3 touches 最优)
- v3.1: S/R Flip - 被突破的阻力变为支撑，被跌破的支撑变为阻力
- v3.1: Round Number 心理整数关口 (Osler 2000)

设计原则:
- 只做预处理，不做交易判断
- 输出结构化数据，让 AI 解读
- 硬风控只在 HIGH strength 时介入
- v2.0: 传递原始数据让 AI 可以验证计算结果
- v3.0: Swing Points 是学术验证最有效的 S/R 来源 (Chan 2022, MDPI)
- v3.1: S/R Flip 确保价格在任何位置都有上下方 S/R 参考

参考:
- Chan (2022): Machine Learning with Support/Resistance (MDPI)
- Osler (2000): Support for Resistance (FRB NY)
- DeepSupp (2025): HDBSCAN for S/R (arXiv:2507.01971)
- QuantStrategy.io: Order Book Depth Analysis
- Analyzing Alpha: Support and Resistance
- TradingAgents: Local preprocessing + AI decision

Author: AlgVex Team
Date: 2026-01
Version: 3.1
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

# Shared data types (extracted to sr_types.py to break circular imports with sub-modules)
from utils.sr_types import SRLevel, SRSourceType, SRCandidate, SRZone  # noqa: F401 — re-exported


class SRZoneCalculator:
    """
    S/R Zone 计算器 v3.0

    v3.0 新功能:
    - Swing Point 检测: Williams Fractal (N-bar pivot high/low)
    - ATR 自适应聚类: 用 ATR 替代固定百分比，适应不同波动率
    - Touch Count 评分: 统计价格触碰次数，2-3 次最优 (Osler 2000)

    使用方法:
    ```python
    calculator = SRZoneCalculator()
    result = calculator.calculate(
        current_price=100000,
        bb_data={'upper': 101500, 'lower': 98500, 'middle': 100000},
        sma_data={'sma_50': 99000, 'sma_200': 95000},
        orderbook_anomalies={'bid_anomalies': [...], 'ask_anomalies': [...]},
        bars_data=[{'high': ..., 'low': ..., 'close': ...}, ...],  # v3.0
        atr_value=1500.0,  # v3.0
    )

    # 输出给 AI
    ai_report = result['ai_report']

    # 硬风控检查
    if result['hard_control']['block_long']:
        # 阻止 LONG
    ```
    """

    # 权重配置
    # v2.1: 降低 Order Wall 权重 (从 2.0 → 0.8)
    # v3.0: 新增 Swing Point 权重 1.2 (介于 BB=1.0 和 SMA_200=1.5 之间)
    WEIGHTS = {
        'Order_Wall': 0.8,      # 订单簿大单 (v2.1: 降低权重，仅作为辅助确认)
        'SMA_200': 1.5,         # 长期趋势 (最重要)
        'Swing_High': 1.2,      # v3.0: Swing Point (结构性，学术验证有效)
        'Swing_Low': 1.2,       # v3.0: Swing Point
        'BB_Upper': 1.0,        # 布林带
        'BB_Lower': 1.0,
        'SMA_50': 0.8,          # 中期趋势
        'Pivot': 0.7,           # Pivot Points (可选)
        'Round_Number': 0.6,    # v3.1: 心理整数关口 (Osler 2000: round numbers attract orders)
    }

    # v2.1: Order Wall 过滤阈值
    ORDER_WALL_THRESHOLDS = {
        'min_btc': 50.0,        # 最小 BTC 阈值 (小于此值不算大单)
        'min_distance_pct': 0.5, # 最小距离阈值 (距离当前价 < 0.5% 的不算 S/R)
        'high_strength_btc': 100.0,  # 达到此 BTC 量才能贡献 HIGH strength
    }

    # 强度阈值
    # v2.1: HIGH 需要 total_weight >= 3.0 或 Order Wall >= 100 BTC
    STRENGTH_THRESHOLDS = {
        'HIGH': 3.0,            # 总权重 >= 3.0 或 Order Wall >= high_strength_btc
        'MEDIUM': 1.5,          # 总权重 >= 1.5
        'LOW': 0.0,             # 其他
    }

    def __init__(
        self,
        cluster_pct: float = 0.5,       # 聚类阈值 (价差 < 0.5% 合并)
        zone_expand_pct: float = 0.1,   # Zone 扩展 (上下各 0.1%)
        hard_control_threshold_pct: float = 1.0,  # 硬风控阈值 (距离 < 1%) — fixed 模式
        # v5.1: ATR-adaptive hard control
        hard_control_threshold_mode: str = "fixed",  # "fixed" or "atr"
        hard_control_atr_multiplier: float = 0.5,    # ATR 模式乘数
        hard_control_atr_min_pct: float = 0.3,       # ATR 模式下限
        hard_control_atr_max_pct: float = 2.0,       # ATR 模式上限
        # v3.0: Swing Point 配置
        swing_detection_enabled: bool = True,
        swing_left_bars: int = 5,       # 左侧 N 根 bar
        swing_right_bars: int = 5,      # 右侧 N 根 bar
        swing_weight: float = 1.2,      # Swing Point 权重
        swing_max_age: int = 100,       # 最大回看 bar 数
        # v3.0: ATR 自适应聚类
        use_atr_adaptive: bool = True,
        atr_cluster_multiplier: float = 0.5,  # cluster_threshold = ATR × multiplier
        # v3.0: Touch Count 配置
        touch_count_enabled: bool = True,
        touch_threshold_atr: float = 0.3,  # 触碰判定距离 = ATR × threshold
        optimal_touches: Tuple[int, ...] = (2, 3),  # 最优触碰次数
        decay_after_touches: int = 4,   # 超过此次数开始衰减
        # v4.0: Aggregation rules (from configs/base.yaml: sr_zones.aggregation.*)
        same_data_weight_cap: float = 2.5,   # 同源封顶
        max_zone_weight: float = 6.0,        # Zone 总权重上限
        confluence_bonus_2: float = 0.2,     # 2 种来源类型交汇奖励
        confluence_bonus_3: float = 0.5,     # 3+ 种来源类型交汇奖励
        # v4.0: Round Number config (from configs/base.yaml: sr_zones.round_number.*)
        round_number_btc_step: int = 5000,   # BTC 心理关口步长 (Osler 2003: $5k)
        round_number_count: int = 3,         # 上下各生成 N 个关口
        logger: logging.Logger = None,
    ):
        """
        初始化计算器

        Parameters
        ----------
        cluster_pct : float
            聚类阈值，价差小于此百分比的候选合并为一个 Zone
        zone_expand_pct : float
            Zone 边界扩展百分比
        hard_control_threshold_pct : float
            硬风控触发阈值 (仅对 HIGH strength)
        swing_detection_enabled : bool
            启用 Swing Point 检测 (v3.0)
        swing_left_bars : int
            Swing Point 左侧 bar 数量
        swing_right_bars : int
            Swing Point 右侧 bar 数量
        swing_weight : float
            Swing Point 权重
        swing_max_age : int
            Swing Point 最大回看 bar 数
        use_atr_adaptive : bool
            使用 ATR 自适应聚类阈值 (v3.0)
        atr_cluster_multiplier : float
            ATR 聚类乘数 (cluster_threshold = ATR × multiplier)
        touch_count_enabled : bool
            启用 Touch Count 评分 (v3.0)
        touch_threshold_atr : float
            触碰判定距离 (ATR 的倍数)
        optimal_touches : Tuple[int, ...]
            最优触碰次数 (权重加成)
        decay_after_touches : int
            超过此次数后权重开始衰减
        logger : logging.Logger
            日志记录器
        """
        self.cluster_pct = cluster_pct
        self.zone_expand_pct = zone_expand_pct
        self.hard_control_threshold_pct = hard_control_threshold_pct

        # v5.1: ATR-adaptive hard control
        self.hard_control_threshold_mode = hard_control_threshold_mode
        self.hard_control_atr_multiplier = hard_control_atr_multiplier
        self.hard_control_atr_min_pct = hard_control_atr_min_pct
        self.hard_control_atr_max_pct = hard_control_atr_max_pct

        # v3.0: Swing Point
        self.swing_detection_enabled = swing_detection_enabled
        self.swing_left_bars = swing_left_bars
        self.swing_right_bars = swing_right_bars
        self.swing_weight = swing_weight
        self.swing_max_age = swing_max_age
        # Update WEIGHTS with configured swing weight
        self.WEIGHTS = dict(self.WEIGHTS)  # Instance copy
        self.WEIGHTS['Swing_High'] = swing_weight
        self.WEIGHTS['Swing_Low'] = swing_weight

        # v3.0: ATR adaptive clustering
        self.use_atr_adaptive = use_atr_adaptive
        self.atr_cluster_multiplier = atr_cluster_multiplier

        # v3.0: Touch count
        self.touch_count_enabled = touch_count_enabled
        self.touch_threshold_atr = touch_threshold_atr
        self.optimal_touches = optimal_touches
        self.decay_after_touches = decay_after_touches

        # v4.0: Aggregation rules (from configs/base.yaml: sr_zones.aggregation.*)
        self._same_data_weight_cap = same_data_weight_cap
        self._max_zone_weight = max_zone_weight
        self._confluence_bonus_2 = confluence_bonus_2
        self._confluence_bonus_3 = confluence_bonus_3

        # v4.0: Round Number config (from configs/base.yaml: sr_zones.round_number.*)
        self._round_number_btc_step = round_number_btc_step
        self._round_number_count = round_number_count

        self.logger = logger or logging.getLogger(__name__)
        # Phase 1.8: Zone stickiness cache — maps 'support'/'resistance' to previous zones.
        # Prevents zone churn between ticks when new zones are within ATR×0.2 of old ones.
        self._zone_stickiness_cache: dict = {}
        # Phase 1.7: Flip discount cache — key=(round(price,0), side), val∈[0.5, 1.0].
        # Starts at 0.5 for newly flipped zones; increments +0.25 per confirmed new-direction
        # touch until reaching 1.0 (fully restored). Persists across calculate() calls.
        self._flip_discount_cache: dict = {}
        # Scratch list populated by _detect_swing_points() for flip candidates discovered
        # in the current cycle; used by calculate() to update _flip_discount_cache.
        self._current_flip_candidates: list = []

    # =========================================================================
    # v3.0: Swing Point Detection (Williams Fractal / N-bar Pivot)
    # =========================================================================

    def _detect_swing_points(
        self,
        bars_data: List[Dict[str, Any]],
        current_price: float,
    ) -> List[SRCandidate]:
        """
        v10.0: Detect swing highs/lows using scipy.signal.find_peaks.
        Replaces hand-written Williams Fractal loop.
        Preserves S/R flip logic + flip discount cache (domain-specific).

        Reference: Chan (2022, MDPI) - swing points improved ML S/R profitability by 65%.
        """
        import numpy as _np
        from scipy.signal import find_peaks as _find_peaks

        candidates = []
        if not bars_data:
            return candidates

        bars = bars_data[-self.swing_max_age:] if len(bars_data) > self.swing_max_age else bars_data
        n = len(bars)
        left = self.swing_left_bars
        min_bars_needed = left + 1 + self.swing_right_bars

        if n < min_bars_needed:
            self.logger.debug(f"Swing detection: insufficient bars ({n} < {min_bars_needed})")
            return candidates

        highs = _np.array([float(b.get('high', 0)) for b in bars])
        lows = _np.array([float(b.get('low', 0)) for b in bars])

        high_peaks, _ = _find_peaks(highs, distance=left)
        low_peaks, _ = _find_peaks(-lows, distance=left)

        def _make_candidate(idx, price_val, is_high):
            bars_ago = n - 1 - idx
            age_factor = max(0.5, 1.0 - (bars_ago / self.swing_max_age) * 0.5)

            if is_high:
                if price_val >= current_price:
                    side = 'resistance'
                    _is_flip = False
                else:
                    side = 'support'
                    _is_flip = True
                source_name = "Swing_High"
                base_weight = self.WEIGHTS.get('Swing_High', 1.0)
            else:
                if price_val <= current_price:
                    side = 'support'
                    _is_flip = False
                else:
                    side = 'resistance'
                    _is_flip = True
                source_name = "Swing_Low"
                base_weight = self.WEIGHTS.get('Swing_Low', 1.0)

            # Phase 1.7: Flip discount cache
            if _is_flip:
                _cache_key = (round(price_val, 0), side)
                _fw = self._flip_discount_cache.get(_cache_key, 0.5)
                self._current_flip_candidates.append((_cache_key, price_val))
            else:
                _fw = 1.0

            return SRCandidate(
                price=price_val,
                source=source_name,
                weight=base_weight * age_factor * _fw,
                side=side,
                extra={
                    'bar_index': idx, 'bars_ago': bars_ago, 'age_factor': age_factor,
                    'is_flipped': _is_flip,
                    'flip_discount': _fw if _is_flip else 1.0,
                },
                level=SRLevel.INTERMEDIATE,
                source_type=SRSourceType.STRUCTURAL,
                timeframe="30m",
            )

        for idx in high_peaks:
            if highs[idx] > 0:
                candidates.append(_make_candidate(idx, float(highs[idx]), is_high=True))
        for idx in low_peaks:
            if lows[idx] > 0:
                candidates.append(_make_candidate(idx, float(lows[idx]), is_high=False))

        self.logger.debug(f"Swing detection: found {len(candidates)} swing points from {n} bars")
        return candidates

    # =========================================================================
    # v3.0: ATR Calculation from Bars
    # =========================================================================

    @staticmethod
    def _calculate_atr_from_bars(
        bars_data: List[Dict[str, Any]],
        period: int = 14,
    ) -> float:
        """
        Calculate ATR (Average True Range) from bar data.

        Used when no external ATR value is provided.

        Parameters
        ----------
        bars_data : List[Dict]
            OHLC bar data.
        period : int
            ATR period (default 14).

        Returns
        -------
        float
            ATR value, or 0.0 if insufficient data.
        """
        if not bars_data or len(bars_data) < 2:
            return 0.0

        true_ranges = []
        for i in range(1, len(bars_data)):
            high = float(bars_data[i].get('high', 0))
            low = float(bars_data[i].get('low', 0))
            prev_close = float(bars_data[i - 1].get('close', 0))

            if high <= 0 or low <= 0 or prev_close <= 0:
                continue

            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close),
            )
            true_ranges.append(tr)

        if not true_ranges:
            return 0.0

        # Wilder's Smoothed Moving Average (RMA/SMMA) — standard ATR formula.
        # Matches indicators/technical_manager.py so S/R zone ATR is consistent
        # with what the AI sees in technical_data.
        # Formula: ATR_t = (ATR_{t-1} × (period-1) + TR_t) / period
        if len(true_ranges) < period:
            return sum(true_ranges) / len(true_ranges)
        atr = sum(true_ranges[:period]) / period  # SMA seed
        for tr in true_ranges[period:]:
            atr = (atr * (period - 1) + tr) / period
        return atr

    # =========================================================================
    # v3.1: Round Number Psychological Levels
    # =========================================================================

    def _generate_round_number_levels(
        self,
        current_price: float,
        count: int = None,
    ) -> List[SRCandidate]:
        """
        Generate round-number psychological S/R levels near current price.

        Round numbers attract limit orders and act as psychological barriers.
        Reference: Osler (2003) "Currency Orders and Exchange Rate Dynamics"
        - $5k/$10k levels for BTC are significant (Osler 2003)

        Parameters
        ----------
        current_price : float
            Current price.
        count : int
            Number of levels above and below to generate.
            If None, uses self._round_number_count (from config).

        Returns
        -------
        List[SRCandidate]
            Round number candidates (both support and resistance).
        """
        candidates = []
        if current_price <= 0:
            return candidates

        if count is None:
            count = self._round_number_count

        # Determine round-number step based on price magnitude
        # v4.0: BTC step from config (Osler 2003: $5k significant, $1k too fine)
        if current_price >= 10000:
            step = self._round_number_btc_step  # BTC: $95000, $100000, $105000... (default $5k)
        elif current_price >= 1000:
            step = 100        # ETH: $3100, $3200...
        elif current_price >= 100:
            step = 10
        elif current_price >= 10:
            step = 1
        else:
            step = 0.1

        # Find the nearest round number below
        base = int(current_price / step) * step

        for i in range(-count, count + 1):
            level_price = base + i * step
            if level_price <= 0:
                continue
            # Skip levels too close to current price (< 0.1%)
            distance_pct = abs(level_price - current_price) / current_price * 100
            if distance_pct < 0.1:
                continue

            side = 'support' if level_price < current_price else 'resistance'
            candidates.append(SRCandidate(
                price=float(level_price),
                source='Round_Number',
                weight=self.WEIGHTS['Round_Number'],
                side=side,
                level=SRLevel.MINOR,
                source_type=SRSourceType.PSYCHOLOGICAL,  # v4.0 (B3): reclassified
                timeframe="static",  # v4.0 (B3)
            ))

        return candidates

    # =========================================================================
    # v3.0: Touch Count for Zones
    # =========================================================================

    def _count_zone_touches(
        self,
        zone_center: float,
        zone_low: float,
        zone_high: float,
        bars_data: List[Dict[str, Any]],
        atr_value: float,
    ) -> int:
        """
        Count discrete touches of a zone (consecutive bars = 1 touch).

        A "touch" requires price to leave the zone and then re-enter it.
        Consecutive bars overlapping the same zone count as a single touch.
        This matches Osler (2000)'s definition where 2-3 discrete visits
        to a price level indicate optimal S/R strength.

        Parameters
        ----------
        zone_center : float
            Center price of the zone.
        zone_low : float
            Lower boundary of the zone.
        zone_high : float
            Upper boundary of the zone.
        bars_data : List[Dict]
            OHLC bar data.
        atr_value : float
            Current ATR value for touch threshold calculation.

        Returns
        -------
        int
            Number of discrete touches.
        """
        if not bars_data or atr_value <= 0:
            return 0

        touch_distance = atr_value * self.touch_threshold_atr
        expanded_low = zone_low - touch_distance
        expanded_high = zone_high + touch_distance

        touches = 0
        was_in_zone = False

        for bar in bars_data:
            bar_high = float(bar.get('high', 0))
            bar_low = float(bar.get('low', 0))

            if bar_high <= 0 or bar_low <= 0:
                continue

            # Bar overlaps with the expanded zone
            in_zone = (bar_low <= expanded_high and bar_high >= expanded_low)

            if in_zone and not was_in_zone:
                # Price just entered the zone — count as a new touch
                touches += 1

            was_in_zone = in_zone

        return touches

    def _touch_weight_bonus(self, touch_count: int) -> float:
        """
        Calculate weight bonus based on touch count.

        2-3 touches: +0.5 weight bonus (optimal, Osler 2000)
        4+  touches: diminishing bonus (zone may be weakening)
        0-1 touches: no bonus

        Parameters
        ----------
        touch_count : int
            Number of price touches.

        Returns
        -------
        float
            Weight bonus to add to zone's total_weight.
        """
        if touch_count in self.optimal_touches:
            return 0.5
        elif touch_count >= self.decay_after_touches:
            # Diminishing: 0.3 for 4, 0.1 for 5, 0 for 6+
            decay = max(0.0, 0.5 - (touch_count - self.decay_after_touches + 1) * 0.2)
            return decay
        elif touch_count == 1:
            return 0.1
        return 0.0

    # =========================================================================
    # v8.0: Hold Probability Estimation
    # =========================================================================

    def _estimate_hold_probability(
        self,
        zone: SRZone,
        current_price: float = 0.0,
        technical_data: Optional[Dict[str, Any]] = None,
        orderbook_data: Optional[Dict[str, Any]] = None,
        bars_data: Optional[List[Dict[str, Any]]] = None,
    ) -> float:
        """
        Estimate the probability that a zone will hold (not be broken through).

        Combines zone attributes with real-time market context.

        v8.2 CALIBRATED from 30-day backtest (2550 touched zones, 2026-02-17):
          Actual hold rates: 59-78% across all zone types.
          Base formula re-anchored to match empirical data.
          Source/touch factors derived from actual hold rates, not academic theory.

        Calibration data (30d, BTCUSDT perpetual):
          By strength: LOW=61.3%, MEDIUM=63.7%, HIGH=68.5%
          By source: STRUCTURAL=63.2%, PROJECTED=67.0%, TECHNICAL=56.1%
          By touches: 0-1=62.7%, 2-3=64.7%, 4=66.1%, 5+=62.3%
          By swing: with=64.0%, without=62.0%
          Overall: 63.5%

        Formula (v8.2 calibrated):
          base_hold = base × touch_factor × source_factor × wall_factor × swing_factor

        v8.1 real-time correction (Phase 3, conservative ranges):
          hold_prob = base_hold × momentum_factor × obi_factor × velocity_factor

        Returns float clamped to [0.50, 0.82].
        """
        from utils.calibration_loader import (
            load_calibration, get_base_formula, get_touch_factor,
            get_source_factor, get_side_factor, get_swing_factor,
        )
        cal = load_calibration()

        # Base probability from total_weight (proxy for zone depth/confluence)
        # Dynamic: reads from data/calibration/latest.json if available
        # Default v8.2: intercept=0.58, slope=0.14 → range [0.58, 0.72]
        intercept, slope = get_base_formula(cal)
        max_w = getattr(self, '_max_zone_weight', 6.0)
        base = min(intercept + slope, intercept + slope * (zone.total_weight / max_w))

        # Touch count factor — dynamic from calibration
        touch_factor = get_touch_factor(zone.touch_count, cal)

        # Source type factor — dynamic from calibration
        source_type_str = zone.source_type if isinstance(zone.source_type, str) else str(zone.source_type)
        source_factor = get_source_factor(source_type_str, cal)

        # Order wall factor (no calibration data, keep conservative)
        if zone.has_order_wall and zone.wall_size_btc >= 100:
            wall_factor = 1.05
        elif zone.has_order_wall and zone.wall_size_btc >= 50:
            wall_factor = 1.02
        else:
            wall_factor = 1.0

        # Swing point — dynamic from calibration
        swing_factor = get_swing_factor(zone.has_swing_point, cal)

        # Side factor (support vs resistance) — dynamic, regime-dependent
        # Only populated by auto-calibration, default=1.0 (no adjustment)
        is_support = zone.price_center < current_price if current_price > 0 else True
        side_str = 'support' if is_support else 'resistance'
        side_factor = get_side_factor(side_str, cal)

        # Combine static factors
        base_hold = base * touch_factor * source_factor * wall_factor * swing_factor * side_factor

        # v8.1: Real-time correction factors (Phase 3)
        # Ranges tightened to ±8% (was ±20-25%) — uncalibrated, be conservative
        momentum_factor = self._compute_momentum_factor(
            zone, current_price, technical_data
        ) if current_price > 0 else 1.0

        obi_factor = self._compute_obi_factor(
            zone, current_price, orderbook_data
        ) if current_price > 0 else 1.0

        velocity_factor = self._compute_volume_velocity_factor(
            zone, current_price, bars_data
        ) if current_price > 0 else 1.0

        # Phase 1.5: Approach momentum — price speed toward zone (K-line derived, zero new deps)
        approach_factor = self._compute_approach_momentum_factor(
            zone, current_price, bars_data
        ) if current_price > 0 else 1.0

        hold_prob = base_hold * momentum_factor * obi_factor * velocity_factor * approach_factor

        return round(max(0.50, min(0.82, hold_prob)), 3)

    # =========================================================================
    # v8.1: Real-time correction factors (Phase 3)
    # =========================================================================

    def _compute_momentum_factor(
        self,
        zone: SRZone,
        current_price: float,
        technical_data: Optional[Dict[str, Any]],
    ) -> float:
        """
        Momentum correction for hold_probability.

        Academic basis:
        - Chung & Bellotti (2021): momentum divergence reduces zone hold rate
        - Spitsin (2025): trend-aligned zones P=0.81-0.88, counter-trend P≈0.55

        Logic:
        - RSI extreme approaching zone → momentum may overwhelm zone
        - MACD histogram direction vs zone type → confirmation or divergence

        Returns: 0.92 - 1.08 multiplier (v8.2: tightened from 0.70-1.20, uncalibrated)
        """
        if not technical_data:
            return 1.0

        rsi = technical_data.get('rsi')
        macd_hist = technical_data.get('macd_histogram')
        factor = 1.0

        is_support = zone.price_center < current_price
        is_resistance = zone.price_center > current_price

        # RSI correction (v8.2: tightened to ±4% per signal, max ±8% total)
        if rsi is not None:
            if is_support and rsi < 25:
                factor *= 0.96
            elif is_support and rsi < 35:
                factor *= 0.98
            elif is_support and rsi > 50:
                factor *= 1.04
            elif is_resistance and rsi > 75:
                factor *= 0.96
            elif is_resistance and rsi > 65:
                factor *= 0.98
            elif is_resistance and rsi < 50:
                factor *= 1.04

        # MACD histogram direction correction (v8.2: ±4%)
        if macd_hist is not None:
            if is_support and macd_hist < 0:
                factor *= 0.96
            elif is_support and macd_hist > 0:
                factor *= 1.04
            elif is_resistance and macd_hist > 0:
                factor *= 0.96
            elif is_resistance and macd_hist < 0:
                factor *= 1.04

        return max(0.92, min(1.08, factor))

    def _compute_obi_factor(
        self,
        zone: SRZone,
        current_price: float,
        orderbook_data: Optional[Dict[str, Any]],
    ) -> float:
        """
        Order Book Imbalance (OBI) correction for hold_probability.

        Academic basis:
        - Cont, Kukanov & Stoikov (2014): OBI linearly predicts short-term price moves
        - Osler (2003): order clustering at S/R zones

        Logic:
        - Buy pressure (positive OBI) at support → zone more likely to hold
        - Sell pressure (negative OBI) at resistance → zone more likely to hold
        - Opposite → zone less likely to hold

        Returns: 0.92 - 1.08 multiplier (v8.2: tightened from 0.75-1.25, uncalibrated)
        """
        if not orderbook_data:
            return 1.0

        obi_data = orderbook_data.get('obi', {})
        dynamics = orderbook_data.get('dynamics', {})
        status = orderbook_data.get('_status', {})

        if status.get('code') != 'OK':
            return 1.0

        obi_value = obi_data.get('adaptive_weighted') or obi_data.get('simple', 0)
        if obi_value == 0:
            return 1.0

        is_support = zone.price_center < current_price
        is_resistance = zone.price_center > current_price
        factor = 1.0

        obi_abs = abs(obi_value)
        obi_strength = min(obi_abs / 0.3, 1.0)

        # v8.2: ±8% max (was ±20%)
        if is_support:
            if obi_value > 0:
                factor = 1.0 + 0.06 * obi_strength
            else:
                factor = 1.0 - 0.06 * obi_strength
        elif is_resistance:
            if obi_value < 0:
                factor = 1.0 + 0.06 * obi_strength
            else:
                factor = 1.0 - 0.06 * obi_strength

        # Dynamics boost (v8.2: ±2%)
        trend = dynamics.get('trend', 'NEUTRAL')
        if is_support and trend == 'BUY_PRESSURE':
            factor *= 1.02
        elif is_resistance and trend == 'SELL_PRESSURE':
            factor *= 1.02

        return max(0.92, min(1.08, factor))

    def _compute_volume_velocity_factor(
        self,
        zone: SRZone,
        current_price: float,
        bars_data: Optional[List[Dict[str, Any]]],
    ) -> float:
        """
        Volume velocity correction for hold_probability.

        Logic:
        - High volume surge toward zone → momentum may overwhelm (weaker hold)
        - Low/declining volume toward zone → momentum fading (stronger hold)
        - Taker buy/sell ratio indicates aggressor direction

        Returns: 0.92 - 1.08 multiplier (v8.2: tightened from 0.80-1.20, uncalibrated)
        """
        if not bars_data or len(bars_data) < 10:
            return 1.0

        recent_bars = bars_data[-5:]
        lookback_bars = bars_data[-20:]

        recent_vol = sum(b.get('volume', 0) for b in recent_bars) / len(recent_bars)
        avg_vol = sum(b.get('volume', 0) for b in lookback_bars) / len(lookback_bars)

        if avg_vol <= 0:
            return 1.0

        vol_ratio = recent_vol / avg_vol

        recent_buy_vol = sum(b.get('taker_buy_volume', 0) for b in recent_bars)
        recent_total_vol = sum(b.get('volume', 0) for b in recent_bars)
        buy_ratio = (recent_buy_vol / recent_total_vol) if recent_total_vol > 0 else 0.5

        is_support = zone.price_center < current_price
        is_resistance = zone.price_center > current_price
        factor = 1.0

        # v8.2: ±6% max (was ±15%)
        if vol_ratio > 2.0:
            if is_support and buy_ratio < 0.40:
                factor = 0.94
            elif is_support and buy_ratio > 0.60:
                factor = 1.06
            elif is_resistance and buy_ratio > 0.60:
                factor = 0.94
            elif is_resistance and buy_ratio < 0.40:
                factor = 1.06
        elif vol_ratio < 0.5:
            factor = 1.04

        return max(0.92, min(1.08, factor))

    def _compute_approach_momentum_factor(
        self,
        zone: SRZone,
        current_price: float,
        bars_data: Optional[List[Dict[str, Any]]],
    ) -> float:
        """
        Phase 1.5: Approach momentum correction for hold_probability.

        High-momentum approach → pending orders get eaten quickly → lower hold_prob.
        Low-momentum / decelerating approach → orders have time to replenish → higher hold_prob.

        Formula:
            approach_speed = |close_now - close_{N_bars_ago}| / (N × ATR)
            N = 4 bars (= 2 hours at 30M) per SR_UPGRADE_PLAN_V2 §1.5

        Data source: existing K-line bars — zero external dependency.

        Returns: 0.94 - 1.06 multiplier (conservative; uncalibrated, ±3% max per side)
        """
        if not bars_data or len(bars_data) < 6:
            return 1.0

        N = 4
        latest_close = float(bars_data[-1].get('close', 0))
        close_n_ago = float(bars_data[-N - 1].get('close', 0))
        if latest_close <= 0 or close_n_ago <= 0:
            return 1.0

        atr = self._calculate_atr_from_bars(bars_data, period=14)
        if atr <= 0:
            return 1.0

        price_delta = latest_close - close_n_ago
        approach_speed = abs(price_delta) / (N * atr)  # in ATR-per-bar units

        is_support = zone.price_center < current_price
        is_resistance = zone.price_center > current_price

        # "Approaching" = price moving TOWARD the zone
        approaching = (is_support and price_delta < 0) or (is_resistance and price_delta > 0)

        factor = 1.0
        if approaching:
            # Fast approach → momentum may overwhelm resting orders
            if approach_speed > 1.5:
                factor = 0.94   # strong: -6%
            elif approach_speed > 0.8:
                factor = 0.97   # moderate: -3%
            # slow approach (speed ≤ 0.8) → no adjustment
        else:
            # Price moving away from or parallel to zone — zone more likely to hold
            if approach_speed < 0.3:
                factor = 1.04   # very slow / sideways: +4%

        return max(0.94, min(1.06, factor))

    # =========================================================================
    # Main Calculation Methods
    # =========================================================================

    def calculate(
        self,
        current_price: float,
        bb_data: Optional[Dict[str, float]] = None,
        sma_data: Optional[Dict[str, float]] = None,
        orderbook_anomalies: Optional[Dict] = None,
        # v3.0: New parameters (backward compatible)
        bars_data: Optional[List[Dict[str, Any]]] = None,
        atr_value: Optional[float] = None,
        # v4.0: MTF bars and additional data sources
        bars_data_4h: Optional[List[Dict[str, Any]]] = None,
        bars_data_1d: Optional[List[Dict[str, Any]]] = None,
        daily_bar: Optional[Dict[str, Any]] = None,
        weekly_bar: Optional[Dict[str, Any]] = None,
        # v8.1: Real-time data for hold_probability correction (Phase 3)
        technical_data: Optional[Dict[str, Any]] = None,
        orderbook_data: Optional[Dict[str, Any]] = None,
        **kwargs,  # v4.0: absorbs old pivot_data from legacy callers
    ) -> Dict[str, Any]:
        """
        计算 S/R Zones

        Parameters
        ----------
        current_price : float
            当前价格
        bb_data : Dict, optional
            布林带数据 {'upper': float, 'lower': float, 'middle': float}
        sma_data : Dict, optional
            SMA 数据 {'sma_50': float, 'sma_200': float}
        orderbook_anomalies : Dict, optional
            订单簿异常数据 {'bid_anomalies': [...], 'ask_anomalies': [...]}
        bars_data : List[Dict], optional
            v3.0: OHLC bar data for swing detection and touch count
        atr_value : float, optional
            v3.0: ATR value for adaptive clustering. If None, calculated from bars_data.
        bars_data_4h : List[Dict], optional
            v4.0: 4H OHLC bars for MTF swing detection
        bars_data_1d : List[Dict], optional
            v4.0: 1D OHLC bars for MTF swing detection
        daily_bar : Dict, optional
            v4.0: Most recent completed daily bar for pivot calculation
        weekly_bar : Dict, optional
            v4.0: Most recent completed weekly bar for pivot calculation

        Returns
        -------
        Dict
            {
                'support_zones': List[SRZone],   # v17.0: max 1 element (nearest)
                'resistance_zones': List[SRZone], # v17.0: max 1 element (nearest)
                'nearest_support': SRZone or None,
                'nearest_resistance': SRZone or None,
                'hard_control': {
                    'block_long': bool,
                    'block_short': bool,
                    'reason': str
                },
                'ai_report': str  # 格式化的 AI 报告
            }
        """
        if current_price <= 0:
            return self._empty_result()

        # Phase 1.7: Reset per-cycle flip candidate scratch list
        self._current_flip_candidates = []

        # v3.0: Calculate ATR if not provided (needed for adaptive clustering + touch count)
        effective_atr = atr_value
        if effective_atr is None and bars_data:
            effective_atr = self._calculate_atr_from_bars(bars_data)
        if effective_atr is None:
            effective_atr = 0.0

        # Step 1: 收集所有候选 (v3.0: Swing Points, v4.0: MTF + Pivots + VP)
        candidates = self._collect_candidates(
            current_price, bb_data, sma_data, orderbook_anomalies,
            bars_data=bars_data,
            bars_data_4h=bars_data_4h,
            bars_data_1d=bars_data_1d,
            daily_bar=daily_bar,
            weekly_bar=weekly_bar,
        )

        if not candidates:
            return self._empty_result()

        # Step 2: 分离 support 和 resistance
        support_candidates = [c for c in candidates if c.side == 'support']
        resistance_candidates = [c for c in candidates if c.side == 'resistance']

        # Step 3: 聚类形成 Zones (v3.0: ATR 自适应)
        support_zones = self._cluster_to_zones(
            support_candidates, current_price, 'support',
            atr_value=effective_atr,
        )
        resistance_zones = self._cluster_to_zones(
            resistance_candidates, current_price, 'resistance',
            atr_value=effective_atr,
        )

        # Step 3.5: v3.0 Touch Count scoring
        if self.touch_count_enabled and bars_data and effective_atr > 0:
            for zone in support_zones + resistance_zones:
                zone.touch_count = self._count_zone_touches(
                    zone.price_center, zone.price_low, zone.price_high,
                    bars_data, effective_atr,
                )
                # Apply touch weight bonus
                bonus = self._touch_weight_bonus(zone.touch_count)
                if bonus > 0:
                    zone.total_weight = round(zone.total_weight + bonus, 2)
                    # Re-evaluate strength after bonus
                    zone.strength = self._evaluate_strength(
                        zone.total_weight, zone.has_order_wall, zone.wall_size_btc
                    )

        # Step 3.6: v8.0 Hold Probability estimation + v8.1 real-time correction
        for zone in support_zones + resistance_zones:
            zone.hold_probability = self._estimate_hold_probability(
                zone,
                current_price=current_price,
                technical_data=technical_data,
                orderbook_data=orderbook_data,
                bars_data=bars_data,
            )

        # Phase 1.7: Update flip_discount_cache based on touch_count of newly created zones.
        # For each flip candidate discovered this cycle, find matching zone by proximity;
        # if zone touch_count ≥ 1 (price held in new direction), increment discount +0.25.
        if self._current_flip_candidates:
            _atr_match = (effective_atr or 0.0) * 0.3
            for (_cache_key, _flip_price) in self._current_flip_candidates:
                _flip_side = _cache_key[1]
                _zone_list = support_zones if _flip_side == 'support' else resistance_zones
                for _z in _zone_list:
                    if abs(_z.price_center - _flip_price) <= max(_atr_match, 50.0):
                        _prev = self._flip_discount_cache.get(_cache_key, 0.5)
                        if _z.touch_count >= 1 and _prev < 1.0:
                            _new_disc = min(1.0, _prev + 0.25 * _z.touch_count)
                            if _new_disc != _prev:
                                self._flip_discount_cache[_cache_key] = _new_disc
                                self.logger.debug(
                                    f"Phase 1.7 flip recovery: ${_flip_price:.0f} "
                                    f"{_flip_side} discount {_prev:.2f}→{_new_disc:.2f} "
                                    f"(touch_count={_z.touch_count})"
                                )
                        elif _cache_key not in self._flip_discount_cache:
                            self._flip_discount_cache[_cache_key] = 0.5
                        break
            self._current_flip_candidates = []  # reset for next cycle

        # Step 4: 排序 (按距离)
        support_zones.sort(key=lambda z: z.distance_pct)
        resistance_zones.sort(key=lambda z: z.distance_pct)

        # Phase 1.8: Zone stickiness — suppress jitter when new zones are within
        # ATR×0.2 of cached zones. Only replaces old zone when new one is meaningfully
        # different, preventing zone churn between consecutive timer ticks.
        support_zones = self._apply_zone_stickiness(
            support_zones, 'support', atr_value or 0.0
        )
        resistance_zones = self._apply_zone_stickiness(
            resistance_zones, 'resistance', atr_value or 0.0
        )

        # Step 5: 确定最近的 S/R
        nearest_support = support_zones[0] if support_zones else None
        nearest_resistance = resistance_zones[0] if resistance_zones else None

        # v17.0: Simplify to 1 support + 1 resistance (nearest qualified zone per side)
        # Internal calculation uses all zones for proper clustering and quality assessment,
        # but output is trimmed to the nearest zone per side after quality filtering.
        # Rationale: all downstream logic only uses nearest_*, extra zones add prompt noise.
        support_zones = [nearest_support] if nearest_support else []
        resistance_zones = [nearest_resistance] if nearest_resistance else []

        # Step 6: 硬风控检查 (v5.1: pass ATR for adaptive threshold)
        hard_control = self._check_hard_control(
            current_price, nearest_support, nearest_resistance,
            atr_value=atr_value or 0.0,
        )

        # Step 7: 生成 AI 报告
        ai_report = self._generate_ai_report(
            current_price, support_zones, resistance_zones,
            nearest_support, nearest_resistance
        )

        return {
            'support_zones': support_zones,
            'resistance_zones': resistance_zones,
            'nearest_support': nearest_support,
            'nearest_resistance': nearest_resistance,
            'hard_control': hard_control,
            'ai_report': ai_report,
        }

    def _apply_zone_stickiness(
        self,
        new_zones: list,
        side: str,
        atr_value: float,
    ) -> list:
        """
        Phase 1.8: Zone stickiness anti-jitter filter.

        For each new zone, if there is a cached zone whose center is within
        ATR×0.2 of the new zone's center, keep the old zone to prevent
        tick-by-tick churn.  Zones that differ by more than ATR×0.2 are
        treated as genuinely new and replace the cache entry.

        Parameters
        ----------
        new_zones : list[SRZone]
            Freshly computed zones (already sorted by distance).
        side : str
            'support' or 'resistance' (used as cache key).
        atr_value : float
            Current ATR; stickiness threshold = ATR × 0.2.

        Returns
        -------
        list[SRZone]
            Stabilised zone list; order preserved.
        """
        if atr_value <= 0:
            # Cannot apply stickiness without ATR; update cache and return as-is
            self._zone_stickiness_cache[side] = new_zones[:]
            return new_zones

        threshold = atr_value * 0.2
        cached = self._zone_stickiness_cache.get(side, [])
        if not cached:
            self._zone_stickiness_cache[side] = new_zones[:]
            return new_zones

        stabilised = []
        for new_zone in new_zones:
            matched_old = None
            for old_zone in cached:
                if abs(new_zone.price_center - old_zone.price_center) <= threshold:
                    matched_old = old_zone
                    break
            if matched_old is not None:
                # Phase 1.8 fix: Keep old zone's stable position (price_center,
                # price_low, price_high) but refresh dynamic attributes that are
                # recomputed each cycle. Without this, hold_probability,
                # touch_count, strength etc. become stale (up to 20 min old).
                matched_old.hold_probability = new_zone.hold_probability
                matched_old.touch_count = new_zone.touch_count
                matched_old.total_weight = new_zone.total_weight
                matched_old.strength = new_zone.strength
                matched_old.has_order_wall = new_zone.has_order_wall
                matched_old.wall_size_btc = new_zone.wall_size_btc
                matched_old.order_walls = new_zone.order_walls
                stabilised.append(matched_old)
            else:
                stabilised.append(new_zone)

        # Update cache with final stabilised list for next call
        self._zone_stickiness_cache[side] = stabilised[:]
        if len(new_zones) != len(stabilised):
            pass  # length preserved by design
        kept = sum(1 for s, n in zip(stabilised, new_zones) if s is not n)
        if kept:
            self.logger.debug(
                f"Phase 1.8 stickiness: kept {kept}/{len(new_zones)} old {side} zones "
                f"(ATR={atr_value:.0f}, threshold={threshold:.0f})"
            )
        return stabilised

    def _evaluate_strength(
        self,
        total_weight: float,
        has_order_wall: bool,
        wall_size_btc: float,
        projected_only: bool = False,
    ) -> str:
        """
        Evaluate zone strength from weight and wall size.

        v4.0 (D3): If zone is PROJECTED-only (all candidates are PROJECTED type),
        cap strength at MEDIUM. PROJECTED zones have no historical trade confirmation.
        """
        high_strength_btc = self.ORDER_WALL_THRESHOLDS['high_strength_btc']
        has_significant_wall = has_order_wall and wall_size_btc >= high_strength_btc

        if has_significant_wall or total_weight >= self.STRENGTH_THRESHOLDS['HIGH']:
            strength = 'HIGH'
        elif total_weight >= self.STRENGTH_THRESHOLDS['MEDIUM']:
            strength = 'MEDIUM'
        else:
            strength = 'LOW'

        # v4.0: PROJECTED-only zones capped at MEDIUM
        if projected_only and strength == 'HIGH':
            strength = 'MEDIUM'

        return strength

    def _collect_candidates(
        self,
        current_price: float,
        bb_data: Optional[Dict],
        sma_data: Optional[Dict],
        orderbook_anomalies: Optional[Dict],
        # v3.0: New parameter
        bars_data: Optional[List[Dict[str, Any]]] = None,
        # v4.0: MTF bars and additional data sources
        bars_data_4h: Optional[List[Dict[str, Any]]] = None,
        bars_data_1d: Optional[List[Dict[str, Any]]] = None,
        daily_bar: Optional[Dict[str, Any]] = None,
        weekly_bar: Optional[Dict[str, Any]] = None,
    ) -> List[SRCandidate]:
        """
        收集所有 S/R 候选价位.

        v3.0: Swing Points
        v4.0: MTF swing detection (1D, 4H, 15M), Pivot Points, Volume Profile
              Each source is wrapped in try/except for per-layer error isolation.
              pivot_data parameter removed — Pivot now calculated by sr_pivot_calculator.
        """
        candidates = []

        # ===== 检测层: MTF Swing Points (per-layer error isolation) =====
        # v4.0: Uses sr_swing_detector with Spitsin (2025) volume weighting
        if self.swing_detection_enabled:
            try:
                from utils.sr_swing_detector import detect_swing_points
            except ImportError:
                detect_swing_points = None
                self.logger.warning("sr_swing_detector not available, using legacy swing detection")

            # v4.0: 1D Swing (highest weight, MAJOR level)
            if bars_data_1d:
                try:
                    if detect_swing_points:
                        candidates.extend(detect_swing_points(
                            bars_data_1d, current_price, timeframe="1d",
                            base_weight=2.0, level=SRLevel.MAJOR,
                            left_bars=self.swing_left_bars, right_bars=self.swing_right_bars,
                            max_age=self.swing_max_age, volume_weighting=True,
                        ))
                    else:
                        for c in self._detect_swing_points(bars_data_1d, current_price):
                            c.weight = 2.0 * (c.extra.get('age_factor', 1.0))
                            c.level = SRLevel.MAJOR
                            c.timeframe = "1d"
                            candidates.append(c)
                except Exception as e:
                    self.logger.warning(f"1D Swing detection failed: {e}")

            # v4.0: 4H Swing (intermediate weight)
            if bars_data_4h:
                try:
                    if detect_swing_points:
                        candidates.extend(detect_swing_points(
                            bars_data_4h, current_price, timeframe="4h",
                            base_weight=1.5, level=SRLevel.INTERMEDIATE,
                            left_bars=self.swing_left_bars, right_bars=self.swing_right_bars,
                            max_age=self.swing_max_age, volume_weighting=True,
                        ))
                    else:
                        for c in self._detect_swing_points(bars_data_4h, current_price):
                            c.weight = 1.5 * (c.extra.get('age_factor', 1.0))
                            c.level = SRLevel.INTERMEDIATE
                            c.timeframe = "4h"
                            candidates.append(c)
                except Exception as e:
                    self.logger.warning(f"4H Swing detection failed: {e}")

            # 30M Swing (volume-weighted if available) — v18.2: 15M→30M
            if bars_data:
                try:
                    if detect_swing_points:
                        candidates.extend(detect_swing_points(
                            bars_data, current_price, timeframe="30m",
                            base_weight=0.8, level=SRLevel.MINOR,
                            left_bars=self.swing_left_bars, right_bars=self.swing_right_bars,
                            max_age=self.swing_max_age, volume_weighting=True,
                        ))
                    else:
                        swing_candidates = self._detect_swing_points(bars_data, current_price)
                        candidates.extend(swing_candidates)
                except Exception as e:
                    self.logger.warning(f"30M Swing detection failed: {e}")

        # ===== 投射层: Pivot Points (v4.0, per-layer error isolation) =====
        if daily_bar or weekly_bar:
            try:
                from utils.sr_pivot_calculator import calculate_pivots
                pivot_candidates = calculate_pivots(daily_bar, weekly_bar, current_price)
                candidates.extend(pivot_candidates)
            except Exception as e:
                self.logger.warning(f"Pivot calculation failed: {e}")

        # ===== 确认层: Volume Profile (v4.0, per-layer error isolation) =====
        if bars_data and len(bars_data) >= 10:
            try:
                from utils.sr_volume_profile import calculate_volume_profile
                vp_candidates = calculate_volume_profile(bars_data, current_price)
                candidates.extend(vp_candidates)
            except Exception as e:
                self.logger.warning(f"Volume Profile calculation failed: {e}")

        # ===== 现有来源: OrderWall, Round# (per-layer error isolation) =====
        # v4.2: BB/SMA 从 S/R 聚合中移除 (学术依据)
        # - Bollinger 本人 Rule 7: "price can walk up/down the bands" → 非 S/R
        # - 0/6 学术论文 (Osler, Spitsin, Chan, Chung, De Angelis, Henderson) 将 BB/SMA 作为 S/R
        # - BB/SMA 保留在 AI detailed report 的 RAW DATA SOURCES 中供 AI 参考

        # Order Book Walls (MINOR level, ORDER_FLOW type - 最实时)
        # v2.1: 添加严格过滤条件，避免盘口普通订单被误识别为 S/R
        try:
            if orderbook_anomalies:
                min_btc = self.ORDER_WALL_THRESHOLDS['min_btc']
                min_distance_pct = self.ORDER_WALL_THRESHOLDS['min_distance_pct']

                # Bid walls = Support
                for wall in orderbook_anomalies.get('bid_anomalies', []):
                    wall_price = wall.get('price', 0)
                    wall_btc = wall.get('volume_btc', 0)

                    # v2.1: 过滤条件
                    if wall_price <= 0 or wall_price >= current_price:
                        continue

                    # 检查最小 BTC 阈值
                    if wall_btc < min_btc:
                        self.logger.debug(
                            f"Skipping bid wall at ${wall_price:.0f}: {wall_btc:.1f} BTC < {min_btc} BTC threshold"
                        )
                        continue

                    # 检查最小距离阈值
                    distance_pct = (current_price - wall_price) / current_price * 100
                    if distance_pct < min_distance_pct:
                        self.logger.debug(
                            f"Skipping bid wall at ${wall_price:.0f}: {distance_pct:.2f}% < {min_distance_pct}% min distance"
                        )
                        continue

                    candidates.append(SRCandidate(
                        price=wall_price,
                        source=f"Order_Wall_${wall_price:.0f}",
                        weight=self.WEIGHTS['Order_Wall'],
                        side='support',
                        extra={
                            'size_btc': wall_btc,
                            'multiplier': wall.get('multiplier', 1),
                        },
                        level=SRLevel.MINOR,
                        source_type=SRSourceType.ORDER_FLOW,
                        timeframe="realtime",  # v4.0 (B3)
                    ))

                # Ask walls = Resistance
                for wall in orderbook_anomalies.get('ask_anomalies', []):
                    wall_price = wall.get('price', 0)
                    wall_btc = wall.get('volume_btc', 0)

                    # v2.1: 过滤条件
                    if wall_price <= 0 or wall_price <= current_price:
                        continue

                    # 检查最小 BTC 阈值
                    if wall_btc < min_btc:
                        self.logger.debug(
                            f"Skipping ask wall at ${wall_price:.0f}: {wall_btc:.1f} BTC < {min_btc} BTC threshold"
                        )
                        continue

                    # 检查最小距离阈值
                    distance_pct = (wall_price - current_price) / current_price * 100
                    if distance_pct < min_distance_pct:
                        self.logger.debug(
                            f"Skipping ask wall at ${wall_price:.0f}: {distance_pct:.2f}% < {min_distance_pct}% min distance"
                        )
                        continue

                    candidates.append(SRCandidate(
                        price=wall_price,
                        source=f"Order_Wall_${wall_price:.0f}",
                        weight=self.WEIGHTS['Order_Wall'],
                        side='resistance',
                        extra={
                            'size_btc': wall_btc,
                            'multiplier': wall.get('multiplier', 1),
                        },
                        level=SRLevel.MINOR,
                        source_type=SRSourceType.ORDER_FLOW,
                        timeframe="realtime",  # v4.0 (B3)
                    ))
        except Exception as e:
            self.logger.warning(f"OrderWall candidates failed: {e}")

        # v4.0: Old pivot_data block removed — Pivots now calculated by sr_pivot_calculator
        # (called in 投射层 section above with daily_bar + weekly_bar)

        # v3.1: Round Number Psychological Levels
        try:
            round_candidates = self._generate_round_number_levels(current_price)
            candidates.extend(round_candidates)
        except Exception as e:
            self.logger.warning(f"Round number candidates failed: {e}")

        return candidates

    def _cluster_to_zones(
        self,
        candidates: List[SRCandidate],
        current_price: float,
        side: str,
        # v3.0: ATR for adaptive clustering
        atr_value: float = 0.0,
    ) -> List[SRZone]:
        """v10.0: 将候选聚类为 Zones (sklearn.DBSCAN 替代手写 single-linkage)"""
        if not candidates:
            return []

        from sklearn.cluster import DBSCAN
        import numpy as np

        # v3.0: Determine effective cluster threshold (eps for DBSCAN)
        if self.use_atr_adaptive and atr_value > 0 and current_price > 0:
            atr_pct = (atr_value / current_price) * 100
            effective_cluster_pct = atr_pct * self.atr_cluster_multiplier
            effective_cluster_pct = max(0.1, min(2.0, effective_cluster_pct))
            self.logger.debug(
                f"ATR adaptive clustering: ATR=${atr_value:.0f} "
                f"({atr_pct:.2f}%), cluster_pct={effective_cluster_pct:.3f}%"
            )
        else:
            effective_cluster_pct = self.cluster_pct

        # DBSCAN on 1D price array
        # eps = max price distance for same cluster (in absolute price units)
        mean_price = np.mean([c.price for c in candidates])
        eps = mean_price * (effective_cluster_pct / 100.0)

        prices = np.array([[c.price] for c in candidates])
        clustering = DBSCAN(eps=eps, min_samples=1).fit(prices)

        # Group candidates by cluster label
        clusters: dict = {}
        for idx, label in enumerate(clustering.labels_):
            clusters.setdefault(label, []).append(candidates[idx])

        zones = []
        for cluster_candidates in clusters.values():
            zone = self._create_zone(cluster_candidates, current_price, side)
            zones.append(zone)

        return zones

    def _create_zone(
        self,
        cluster: List[SRCandidate],
        current_price: float,
        side: str,
    ) -> SRZone:
        """从候选 cluster 创建 Zone (v3.0: 添加 swing_point/touch_count)"""
        prices = [c.price for c in cluster]
        price_center = sum(prices) / len(prices)

        # Zone 边界 (扩展)
        expand = price_center * self.zone_expand_pct / 100
        price_low = min(prices) - expand
        price_high = max(prices) + expand

        # 来源列表
        sources = [c.source for c in cluster]

        # v4.0 (D2): Same-source weight capping + multi-source bonus + total cap
        # Step 1: Group by timeframe, cap each group
        same_data_weight_cap = getattr(self, '_same_data_weight_cap', 2.5)
        weight_by_timeframe = {}
        for c in cluster:
            tf = c.timeframe or "unknown"
            weight_by_timeframe.setdefault(tf, 0.0)
            weight_by_timeframe[tf] = min(
                weight_by_timeframe[tf] + c.weight,
                same_data_weight_cap
            )
        total_weight = sum(weight_by_timeframe.values())

        # Step 2: Multi-source independence bonus (from config)
        unique_source_types = len(set(c.source_type for c in cluster))
        if unique_source_types >= 3:
            total_weight += getattr(self, '_confluence_bonus_3', 0.5)
        elif unique_source_types >= 2:
            total_weight += getattr(self, '_confluence_bonus_2', 0.2)

        # Step 3: Total weight cap
        max_zone_weight = getattr(self, '_max_zone_weight', 6.0)
        total_weight = min(total_weight, max_zone_weight)

        # 是否有 Order Wall
        has_order_wall = any('Order_Wall' in c.source for c in cluster)
        wall_size_btc = sum(
            c.extra.get('size_btc', 0)
            for c in cluster
            if 'Order_Wall' in c.source
        )

        # v2.0: 收集 Order Wall 详情
        order_walls = []
        for c in cluster:
            if 'Order_Wall' in c.source:
                order_walls.append({
                    'price': c.price,
                    'size_btc': c.extra.get('size_btc', 0),
                    'multiplier': c.extra.get('multiplier', 1),
                })

        # v3.0: Check for swing points
        has_swing_point = any('Swing_' in c.source for c in cluster)

        # v4.0 (D3): Evaluate strength with PROJECTED cap
        has_projected_only = all(c.source_type == SRSourceType.PROJECTED for c in cluster)
        strength = self._evaluate_strength(total_weight, has_order_wall, wall_size_btc,
                                           projected_only=has_projected_only)

        # 距离当前价格
        if side == 'support':
            distance_pct = (current_price - price_center) / current_price * 100
        else:
            distance_pct = (price_center - current_price) / current_price * 100

        # v2.0: 确定 Zone 的级别 (取最高级别)
        level_priority = {SRLevel.MAJOR: 3, SRLevel.INTERMEDIATE: 2, SRLevel.MINOR: 1}
        zone_level = SRLevel.MINOR
        for c in cluster:
            if level_priority.get(c.level, 0) > level_priority.get(zone_level, 0):
                zone_level = c.level

        # v2.0: 确定主要来源类型 (ORDER_FLOW > STRUCTURAL > PROJECTED > TECHNICAL > PSYCHOLOGICAL)
        # v3.0: STRUCTURAL priority raised (swing points are strong signals)
        # v4.0 (B4): Added PROJECTED and PSYCHOLOGICAL
        type_priority = {
            SRSourceType.ORDER_FLOW: 4,
            SRSourceType.STRUCTURAL: 3,
            SRSourceType.PROJECTED: 2,
            SRSourceType.TECHNICAL: 1,
            SRSourceType.PSYCHOLOGICAL: 0,
        }
        zone_source_type = SRSourceType.TECHNICAL
        for c in cluster:
            if type_priority.get(c.source_type, 0) > type_priority.get(zone_source_type, 0):
                zone_source_type = c.source_type

        return SRZone(
            price_low=round(price_low, 2),
            price_high=round(price_high, 2),
            price_center=round(price_center, 2),
            side=side,
            strength=strength,
            sources=sources,
            total_weight=round(total_weight, 2),
            distance_pct=round(distance_pct, 2),
            has_order_wall=has_order_wall,
            wall_size_btc=round(wall_size_btc, 2),
            level=zone_level,
            source_type=zone_source_type,
            order_walls=order_walls,
            touch_count=0,  # Filled in calculate() after clustering
            has_swing_point=has_swing_point,
        )

    def _get_hard_control_threshold(
        self,
        current_price: float,
        atr_value: float = 0.0,
    ) -> float:
        """
        v5.1: 计算硬风控阈值 (支持 ATR 自适应)

        ATR 模式: threshold = clamp(ATR/price * 100 * multiplier, min, max)
        Fixed 模式: threshold = hard_control_threshold_pct (默认 1.0%)

        Returns
        -------
        float
            阈值百分比 (例如 1.0 表示 1%)
        """
        if self.hard_control_threshold_mode == "atr" and atr_value > 0 and current_price > 0:
            atr_pct = (atr_value / current_price) * 100 * self.hard_control_atr_multiplier
            threshold = max(self.hard_control_atr_min_pct,
                            min(atr_pct, self.hard_control_atr_max_pct))
            self.logger.debug(
                f"Hard control threshold (ATR): {threshold:.2f}% "
                f"(ATR=${atr_value:,.0f}, raw={atr_pct:.2f}%, "
                f"clamped to [{self.hard_control_atr_min_pct}, {self.hard_control_atr_max_pct}])"
            )
            return threshold

        return self.hard_control_threshold_pct

    def _check_hard_control(
        self,
        current_price: float,
        nearest_support: Optional[SRZone],
        nearest_resistance: Optional[SRZone],
        atr_value: float = 0.0,
    ) -> Dict[str, Any]:
        """
        硬风控检查

        只在 HIGH strength 且距离 < threshold 时阻止
        v5.1: threshold 支持 ATR 自适应 (高波动 → 更宽阈值, 低波动 → 更窄阈值)
        """
        block_long = False
        block_short = False
        reasons = []

        threshold = self._get_hard_control_threshold(current_price, atr_value)

        # 检查阻力位 (阻止 LONG)
        if nearest_resistance and nearest_resistance.strength == 'HIGH':
            if nearest_resistance.distance_pct < threshold:
                block_long = True
                if nearest_resistance.has_order_wall:
                    reasons.append(
                        f"LONG blocked: Order Wall at ${nearest_resistance.price_center:,.0f} "
                        f"({nearest_resistance.wall_size_btc:.1f} BTC), "
                        f"{nearest_resistance.distance_pct:.1f}% away (threshold: {threshold:.1f}%)"
                    )
                else:
                    reasons.append(
                        f"LONG blocked: HIGH strength resistance at ${nearest_resistance.price_center:,.0f} "
                        f"(sources: {', '.join(nearest_resistance.sources)}), "
                        f"{nearest_resistance.distance_pct:.1f}% away (threshold: {threshold:.1f}%)"
                    )

        # 检查支撑位 (阻止 SHORT)
        if nearest_support and nearest_support.strength == 'HIGH':
            if nearest_support.distance_pct < threshold:
                block_short = True
                if nearest_support.has_order_wall:
                    reasons.append(
                        f"SHORT blocked: Order Wall at ${nearest_support.price_center:,.0f} "
                        f"({nearest_support.wall_size_btc:.1f} BTC), "
                        f"{nearest_support.distance_pct:.1f}% away (threshold: {threshold:.1f}%)"
                    )
                else:
                    reasons.append(
                        f"SHORT blocked: HIGH strength support at ${nearest_support.price_center:,.0f} "
                        f"(sources: {', '.join(nearest_support.sources)}), "
                        f"{nearest_support.distance_pct:.1f}% away (threshold: {threshold:.1f}%)"
                    )

        return {
            'block_long': block_long,
            'block_short': block_short,
            'reason': '; '.join(reasons) if reasons else None,
        }

    def _generate_ai_report(
        self,
        current_price: float,
        support_zones: List[SRZone],
        resistance_zones: List[SRZone],
        nearest_support: Optional[SRZone],
        nearest_resistance: Optional[SRZone],
    ) -> str:
        """生成 AI 报告 (v3.0: 包含 swing/touch 信息)"""
        parts = ["SUPPORT/RESISTANCE ZONES:"]
        parts.append("")

        # 最近阻力
        if nearest_resistance:
            wall_info = f" [Order Wall: {nearest_resistance.wall_size_btc:.1f} BTC]" if nearest_resistance.has_order_wall else ""
            swing_info = " [Swing Point]" if nearest_resistance.has_swing_point else ""
            touch_info = f" [Touches: {nearest_resistance.touch_count}]" if nearest_resistance.touch_count > 0 else ""
            hold_info = f" [Hold: {nearest_resistance.hold_probability:.0%}]" if nearest_resistance.hold_probability > 0 else ""
            parts.append(f"Nearest RESISTANCE: ${nearest_resistance.price_center:,.0f} "
                        f"({nearest_resistance.distance_pct:.1f}% away) "
                        f"[{nearest_resistance.strength}]{wall_info}{swing_info}{touch_info}{hold_info}")
            parts.append(f"  Zone: ${nearest_resistance.price_low:,.0f} - ${nearest_resistance.price_high:,.0f}")
            parts.append(f"  Sources: {', '.join(nearest_resistance.sources)}")
        else:
            parts.append("Nearest RESISTANCE: None detected")

        parts.append("")

        # 最近支撑
        if nearest_support:
            wall_info = f" [Order Wall: {nearest_support.wall_size_btc:.1f} BTC]" if nearest_support.has_order_wall else ""
            swing_info = " [Swing Point]" if nearest_support.has_swing_point else ""
            touch_info = f" [Touches: {nearest_support.touch_count}]" if nearest_support.touch_count > 0 else ""
            hold_info = f" [Hold: {nearest_support.hold_probability:.0%}]" if nearest_support.hold_probability > 0 else ""
            parts.append(f"Nearest SUPPORT: ${nearest_support.price_center:,.0f} "
                        f"({nearest_support.distance_pct:.1f}% away) "
                        f"[{nearest_support.strength}]{wall_info}{swing_info}{touch_info}{hold_info}")
            parts.append(f"  Zone: ${nearest_support.price_low:,.0f} - ${nearest_support.price_high:,.0f}")
            parts.append(f"  Sources: {', '.join(nearest_support.sources)}")
        else:
            parts.append("Nearest SUPPORT: None detected")

        return "\n".join(parts)

    def _empty_result(self) -> Dict[str, Any]:
        """返回空结果"""
        return {
            'support_zones': [],
            'resistance_zones': [],
            'nearest_support': None,
            'nearest_resistance': None,
            'hard_control': {
                'block_long': False,
                'block_short': False,
                'reason': None,
            },
            'ai_report': "SUPPORT/RESISTANCE ZONES: Data not available",
        }

