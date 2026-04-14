# utils/sr_types.py
"""
Shared S/R data types used by sr_zone_calculator and its sub-modules.

Extracted to break circular dependency:
  sr_zone_calculator → sr_pivot_calculator/sr_volume_profile/sr_swing_detector → sr_zone_calculator
"""

from typing import List, Dict
from dataclasses import dataclass, field


# =============================================================================
# Level and Source Type Enums
# =============================================================================

class SRLevel:
    """S/R Zone 时间框架级别"""
    MAJOR = "MAJOR"           # 日线/周线级别 (SMA_200, 周线 BB)
    INTERMEDIATE = "INTERMEDIATE"  # 4H 级别 (SMA_50, 4H BB)
    MINOR = "MINOR"           # 30M/1H 级别 (SMA_20, BB, Order Wall)


class SRSourceType:
    """S/R 来源类型"""
    ORDER_FLOW = "ORDER_FLOW"       # 订单流 (Order Wall) - 最实时
    TECHNICAL = "TECHNICAL"         # 技术指标 (SMA, BB) - 广泛认可
    STRUCTURAL = "STRUCTURAL"       # 结构性 (前高/前低, Swing Point) - 历史验证
    PROJECTED = "PROJECTED"         # v4.0: 数学投射 (Pivot Points) - 无历史确认
    PSYCHOLOGICAL = "PSYCHOLOGICAL" # v4.0: 心理关口 (Round Numbers)


@dataclass
class SRCandidate:
    """S/R 候选价位"""
    price: float
    source: str          # 来源: BB_Lower, BB_Upper, SMA_50, SMA_200, Order_Wall, Swing_High, Swing_Low, Round_Number
    weight: float        # 权重: Order_Wall=0.8, SMA_200=1.5, Swing=1.2, BB=1.0, SMA_50=0.8
    side: str            # support 或 resistance
    extra: Dict = field(default_factory=dict)  # 额外信息 (如 wall size, bar_index)
    # v2.0 新增
    level: str = SRLevel.MINOR           # 时间框架级别
    source_type: str = SRSourceType.TECHNICAL  # 来源类型
    # v4.0 新增: 用于同源封顶 — 同 timeframe 的候选权重和不超过 SAME_DATA_WEIGHT_CAP
    timeframe: str = ""  # "1d", "4h", "30m", "daily_pivot", "weekly_pivot", "30m_vp", "realtime", "static"


@dataclass
class SRZone:
    """S/R Zone (聚类后的区域)"""
    price_low: float
    price_high: float
    price_center: float
    side: str            # support 或 resistance
    strength: str        # HIGH, MEDIUM, LOW
    sources: List[str]   # 来源列表
    total_weight: float  # 总权重
    distance_pct: float  # 距离当前价格的百分比
    has_order_wall: bool # 是否包含订单簿大单
    wall_size_btc: float # 大单总量 (BTC)
    # v2.0 新增
    level: str = SRLevel.MINOR           # 时间框架级别 (取最高级别)
    source_type: str = SRSourceType.TECHNICAL  # 主要来源类型
    order_walls: List[Dict] = field(default_factory=list)  # Order Wall 详情
    # v3.0 新增
    touch_count: int = 0                 # 价格触碰次数 (2-3 最优)
    has_swing_point: bool = False        # 是否包含 Swing Point
    # v8.0 新增: Hold Probability (维持概率)
    hold_probability: float = 0.0        # Zone 维持（不被突破）的概率 [0.0-1.0]
