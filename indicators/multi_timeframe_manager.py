"""
Multi-Timeframe Indicator Manager v3.3

管理多个时间框架的技术指标，提供跨周期分析能力。

v3.3 更新:
- 移除本地决策逻辑 (ALLOW_LONG/SHORT/WAIT)
- 移除 DecisionState 枚举和相关方法
- 移除 get_summary(), check_execution_confirmation() 等幽灵代码
- 所有决策交由 AI 完成，本地仅提供数据

v3.0 更新:
- 移除对不存在的 ConfigManager 辅助方法的依赖
- 使用 MACD 替代 ADX (ADX 未在 TechnicalIndicatorManager 实现)
- 添加 SMA_200 支持 (需要在 TechnicalIndicatorManager 初始化时指定)
"""

from typing import Dict, Any, Optional
import logging

from nautilus_trader.model.data import Bar, BarType
from indicators.technical_manager import TechnicalIndicatorManager


class MultiTimeframeManager:
    """
    多时间框架管理器 v3.3

    管理三层时间框架:
    - trend_layer (1D): 提供趋势数据给 AI 分析
    - decision_layer (4H): 方向决策 (AI 控制)
    - execution_layer (30M): 入场执行

    v3.3: 仅负责数据收集和路由，所有决策交由 AI 完成
    """

    def __init__(
        self,
        config: Dict[str, Any],
        trend_bar_type: Optional[BarType] = None,
        decision_bar_type: Optional[BarType] = None,
        execution_bar_type: Optional[BarType] = None,
        logger: Optional[logging.Logger] = None,
    ):
        """
        初始化多时间框架管理器

        Parameters
        ----------
        config : Dict
            多时间框架配置 (从 ConfigManager.get('multi_timeframe') 获取)
        trend_bar_type : BarType
            趋势层 BarType (用于精确匹配)
        decision_bar_type : BarType
            决策层 BarType
        execution_bar_type : BarType
            执行层 BarType
        logger : Logger
            日志记录器
        """
        self.config = config
        self.enabled = config.get('enabled', False)
        self.logger = logger or logging.getLogger(__name__)

        # 存储 BarType 用于精确匹配
        self.trend_bar_type = trend_bar_type
        self.decision_bar_type = decision_bar_type
        self.execution_bar_type = execution_bar_type

        if not self.enabled:
            self.logger.info("MultiTimeframeManager: disabled")
            # 初始化为 None 以避免属性访问错误
            self.trend_manager = None
            self.decision_manager = None
            self.execution_manager = None
            return

        # 初始化三层指标管理器
        self.trend_manager: Optional[TechnicalIndicatorManager] = None
        self.decision_manager: Optional[TechnicalIndicatorManager] = None
        self.execution_manager: Optional[TechnicalIndicatorManager] = None

        # 初始化各层管理器
        self._init_managers()

        self.logger.info("MultiTimeframeManager: initialized with 3 layers")

    def _init_managers(self):
        """
        初始化各层技术指标管理器

        v3.2.7 修正: 必须传递所有必需参数，确保指标正确初始化
        v3.2.10 修正: 从配置读取参数，移除硬编码
        TechnicalIndicatorManager 参数参考 indicators/technical_manager.py:29-40
        """
        trend_config = self.config.get('trend_layer', {})
        decision_config = self.config.get('decision_layer', {})
        exec_config = self.config.get('execution_layer', {})

        # 全局默认指标参数 (从 configs/base.yaml indicators 部分读取)
        global_indicators = self.config.get('global_indicators', {})
        default_ema_periods = global_indicators.get('ema_periods', [12, 26])
        default_rsi_period = global_indicators.get('rsi_period', 14)
        default_macd_fast = global_indicators.get('macd_fast', 12)
        default_macd_slow = global_indicators.get('macd_slow', 26)
        default_macd_signal = global_indicators.get('macd_signal', 9)
        default_bb_period = global_indicators.get('bb_period', 20)
        default_bb_std = global_indicators.get('bb_std', 2.0)
        default_volume_ma_period = global_indicators.get('volume_ma_period', 20)
        default_support_resistance_lookback = global_indicators.get('support_resistance_lookback', 20)

        # ========================================
        # 趋势层 (1D) - 需要 SMA_200 用于趋势判断
        # 关键: SMA_200 需要至少 200 根 bar 才能计算
        # ========================================
        sma_period = trend_config.get('sma_period', 200)
        self.trend_manager = TechnicalIndicatorManager(
            sma_periods=[sma_period],      # SMA_200 用于趋势判断
            ema_periods=default_ema_periods,
            rsi_period=default_rsi_period,
            macd_fast=default_macd_fast,
            macd_slow=default_macd_slow,
            macd_signal=default_macd_signal,
            bb_period=default_bb_period,
            bb_std=default_bb_std,
            volume_ma_period=default_volume_ma_period,
            support_resistance_lookback=default_support_resistance_lookback,
        )
        self.logger.debug(f"趋势层管理器初始化: SMA_{sma_period}")

        # ========================================
        # 决策层 (4H) - Bull/Bear 辩论使用的指标
        # 从 decision_layer.indicators 读取配置
        # ========================================
        decision_indicators = decision_config.get('indicators', {})
        self.decision_manager = TechnicalIndicatorManager(
            sma_periods=decision_indicators.get('sma_periods', [20, 50]),
            ema_periods=default_ema_periods,
            rsi_period=decision_indicators.get('rsi_period', default_rsi_period),
            macd_fast=decision_indicators.get('macd_fast', default_macd_fast),
            macd_slow=decision_indicators.get('macd_slow', default_macd_slow),
            macd_signal=default_macd_signal,
            bb_period=decision_indicators.get('bb_period', default_bb_period),
            bb_std=decision_indicators.get('bb_std', default_bb_std),
            volume_ma_period=default_volume_ma_period,
            support_resistance_lookback=default_support_resistance_lookback,
        )
        self.logger.debug("决策层管理器初始化")

        # ========================================
        # 执行层 (30M) - 入场确认指标
        # 从 execution_layer.indicators 读取配置
        # ========================================
        exec_indicators = exec_config.get('indicators', {})
        self.execution_manager = TechnicalIndicatorManager(
            sma_periods=exec_indicators.get('sma_periods', [5, 20]),
            ema_periods=exec_indicators.get('ema_periods', [10, 20]),
            rsi_period=exec_indicators.get('rsi_period', default_rsi_period),
            macd_fast=default_macd_fast,
            macd_slow=default_macd_slow,
            macd_signal=default_macd_signal,
            bb_period=default_bb_period,
            bb_std=default_bb_std,
            volume_ma_period=default_volume_ma_period,
            support_resistance_lookback=exec_indicators.get('support_resistance_lookback', default_support_resistance_lookback),
        )
        self.logger.debug("执行层管理器初始化")

    def is_initialized(self, layer: str = None) -> bool:
        """
        v3.2.7 新增: 检查指标管理器是否已初始化

        Parameters
        ----------
        layer : str, optional
            指定层级 ("trend"/"decision"/"execution")，None 检查全部

        Returns
        -------
        bool
            是否所有指定层级都已初始化 (有足够的 bar 数据)
        """
        if not self.enabled:
            return False

        min_bars = {
            'trend': 200,      # SMA_200 需要 200 根
            'decision': 50,    # SMA_50 需要 50 根
            'execution': 20,   # RSI_14 + EMA_10 需要 ~20 根
        }

        managers = {
            'trend': self.trend_manager,
            'decision': self.decision_manager,
            'execution': self.execution_manager,
        }

        if layer:
            if layer not in managers:
                return False
            mgr = managers[layer]
            if mgr is None:
                return False
            bars_count = len(mgr.recent_bars) if hasattr(mgr, 'recent_bars') else 0
            return bars_count >= min_bars.get(layer, 0)

        # 检查全部
        for name, mgr in managers.items():
            if mgr is None:
                return False
            bars_count = len(mgr.recent_bars) if hasattr(mgr, 'recent_bars') else 0
            if bars_count < min_bars.get(name, 0):
                self.logger.debug(f"{name} 层未初始化: {bars_count}/{min_bars[name]} bars")
                return False

        return True

    def route_bar(self, bar: Bar) -> str:
        """
        路由 bar 到对应的管理器 (精确 BarType 匹配)

        Parameters
        ----------
        bar : Bar
            接收到的 bar 数据

        Returns
        -------
        str
            路由目标: "trend" / "decision" / "execution" / "unknown" / "disabled"
        """
        if not self.enabled:
            return "disabled"

        # 使用精确的 BarType 匹配
        if self.trend_bar_type and bar.bar_type == self.trend_bar_type:
            if self.trend_manager:
                self.trend_manager.update(bar)
            self.logger.debug(f"[1D] 趋势层 bar 更新: close={bar.close}")
            return "trend"

        elif self.decision_bar_type and bar.bar_type == self.decision_bar_type:
            if self.decision_manager:
                self.decision_manager.update(bar)
            self.logger.debug(f"[4H] 决策层 bar 更新: close={bar.close}")
            return "decision"

        elif self.execution_bar_type and bar.bar_type == self.execution_bar_type:
            if self.execution_manager:
                self.execution_manager.update(bar)
            self.logger.debug(f"[30M] 执行层 bar 更新: close={bar.close}")
            return "execution"

        else:
            self.logger.warning(f"Unknown bar type: {bar.bar_type}")
            return "unknown"

    def get_technical_data_for_layer(self, layer: str, current_price: float) -> Dict[str, Any]:
        """
        获取指定层的技术数据

        Parameters
        ----------
        layer : str
            "trend" / "decision" / "execution"
        current_price : float
            当前价格

        Returns
        -------
        Dict
            技术指标数据
        """
        manager = {
            "trend": self.trend_manager,
            "decision": self.decision_manager,
            "execution": self.execution_manager,
        }.get(layer)

        if manager and manager.is_initialized():
            data = manager.get_technical_data(current_price)
            data['_layer'] = layer
            data['_timeframe'] = {
                'trend': '1D',
                'decision': '4H',
                'execution': '30M',
            }.get(layer, 'unknown')
            return data
        return {'_layer': layer, '_initialized': False}

    def is_all_layers_initialized(self) -> bool:
        """检查所有层是否都已初始化"""
        if not self.enabled:
            return True

        return (
            self.trend_manager is not None and self.trend_manager.is_initialized() and
            self.decision_manager is not None and self.decision_manager.is_initialized() and
            self.execution_manager is not None and self.execution_manager.is_initialized()
        )

